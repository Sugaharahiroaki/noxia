"""
カード決済端末との連携。

NOXIA のサーバー → 決済会社のサーバー → 店の決済端末、の順に金額を送り、結果を受け取る（クラウド連携）。
端末を PC・iPad に直接つながないので、どちらの画面からでも同じように使える。

- 決済会社ごとの違いは providers.py の「接続部品」に閉じ込める（模擬端末・Square ターミナル）。
- カード番号などのカード情報はこちらでは一切扱わない・保存しない（端末と決済会社の間だけで処理される）。
- 認証情報（アクセストークン等）はサーバー側だけで保持し、画面には末尾しか返さない。
"""
from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.db import models

from apps.core.models import Store, TimestampedModel


class TerminalProviderKind(models.TextChoices):
    NONE = "none", "未接続（端末に手入力）"
    MOCK = "mock", "模擬端末（テスト用・実際の決済なし）"
    SQUARE = "square", "Square ターミナル"


class TerminalConfig(TimestampedModel):
    """店舗ごとの決済端末の接続設定。"""

    store = models.OneToOneField(Store, on_delete=models.CASCADE, related_name="terminal_config")
    provider = models.CharField(max_length=20, choices=TerminalProviderKind.choices, default=TerminalProviderKind.NONE)
    # Square
    square_environment = models.CharField(max_length=20, default="sandbox")  # sandbox / production
    square_access_token = models.TextField(blank=True)
    square_location_id = models.CharField(max_length=64, blank=True)
    square_webhook_signature_key = models.CharField(max_length=128, blank=True)
    square_webhook_url = models.CharField("Square に登録した通知先URL（署名の検証に使う）", max_length=300, blank=True)
    note = models.CharField(max_length=300, blank=True)


class TerminalStatus(models.TextChoices):
    UNPAIRED = "unpaired", "未接続（ペアリング前）"
    PAIRING = "pairing", "ペアリング中"
    PAIRED = "paired", "接続済み"
    DISABLED = "disabled", "使わない"


class CardTerminal(TimestampedModel):
    """店に置いてある決済端末（1店に複数台も可）。"""

    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="card_terminals")
    provider = models.CharField(max_length=20, choices=TerminalProviderKind.choices)
    name = models.CharField("端末名（例: レジ横）", max_length=40)
    device_id = models.CharField(max_length=80, blank=True)
    status = models.CharField(max_length=20, choices=TerminalStatus.choices, default=TerminalStatus.UNPAIRED)
    pairing_code = models.CharField(max_length=20, blank=True)
    pairing_code_id = models.CharField(max_length=80, blank=True)
    pairing_expires_at = models.DateTimeField(null=True, blank=True)
    paired_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["store_id", "id"]
        constraints = [models.UniqueConstraint(fields=["store", "name"], name="uniq_terminal_name_per_store")]

    def __str__(self) -> str:
        return self.name


class TerminalPaymentStatus(models.TextChoices):
    PENDING = "pending", "端末でカード読み取り待ち"
    APPROVED = "approved", "端末で承認済み（会計を作成中）"
    COMPLETED = "completed", "会計まで完了"
    DECLINED = "declined", "端末で拒否・失敗"
    CANCELED = "canceled", "キャンセル"
    NEEDS_ATTENTION = "needs_attention", "要確認（端末では承認済みだが会計を作れなかった）"


ACTIVE_TERMINAL_STATUSES = (TerminalPaymentStatus.PENDING, TerminalPaymentStatus.APPROVED)


class TerminalPayment(TimestampedModel):
    """
    「決済端末に送る」1回分。端末で承認されたら、送った時点の明細（line_ids）で会計を自動で確定する。
    送った後に伝票に追加された注文は、この会計には含めない（端末で請求した金額と必ず一致させるため）。
    """

    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="terminal_payments")
    visit = models.ForeignKey("sales.Visit", on_delete=models.PROTECT, related_name="terminal_payments")
    terminal = models.ForeignKey(CardTerminal, on_delete=models.PROTECT, related_name="payments")
    provider = models.CharField(max_length=20, choices=TerminalProviderKind.choices)
    amount = models.DecimalField("端末で請求するカード金額", max_digits=12, decimal_places=2)
    cash_amount = models.DecimalField("同時に受け取る現金（現金＋カード）", max_digits=12, decimal_places=2, default=Decimal("0"))
    cash_tendered = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    line_ids = models.JSONField(default=list)
    status = models.CharField(max_length=20, choices=TerminalPaymentStatus.choices, default=TerminalPaymentStatus.PENDING)
    provider_checkout_id = models.CharField(max_length=100, blank=True)
    provider_payment_id = models.CharField(max_length=100, blank=True)
    error_message = models.CharField(max_length=300, blank=True)
    checkout = models.OneToOneField("sales.Checkout", null=True, blank=True, on_delete=models.SET_NULL, related_name="terminal_payment")
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    finished_at = models.DateTimeField(null=True, blank=True)
    # 「要確認」をスタッフが確認して対応した記録（手入力で会計した・端末で返金した 等）
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    resolution_note = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["-id"]
        indexes = [models.Index(fields=["store", "status"]), models.Index(fields=["store", "visit"]), models.Index(fields=["provider_checkout_id"])]


class TerminalWebhookEvent(models.Model):
    """決済会社からの通知（重複して届いても1回だけ処理する）。"""

    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="terminal_webhook_events")
    event_id = models.CharField(max_length=100)
    event_type = models.CharField(max_length=60, blank=True)
    payload = models.JSONField(default=dict)
    received_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["store", "event_id"], name="uniq_terminal_webhook_event")]
