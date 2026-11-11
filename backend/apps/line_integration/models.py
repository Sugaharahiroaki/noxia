"""
LINE公式アカウント連携（将来接続できる設計 + 内部機能）。

- 接続情報は店舗別に保持し、シークレット・トークンはサーバー側のみで扱う（API では末尾だけ返す）。
- 顧客と LINE 利用者は明示的に紐付ける（自動一致させない）。
- 送信はプロバイダ経由。認証情報がない／LINE_SEND_ENABLED が false なら MockProvider が記録だけ行う。
- Webhook は署名検証と重複イベント対策（webhook_event_id の一意制約）を行う。
"""
from __future__ import annotations

from django.conf import settings
from django.db import models

from apps.core.models import Store, TimestampedModel


class LineIntegration(TimestampedModel):
    STATUS_NOT_CONNECTED = "not_connected"
    STATUS_MOCK = "mock"
    STATUS_CONNECTED = "connected"

    store = models.OneToOneField(Store, on_delete=models.CASCADE, related_name="line_integration")
    channel_id = models.CharField(max_length=40, blank=True)
    channel_secret = models.CharField(max_length=120, blank=True)
    channel_access_token = models.TextField(blank=True)
    status = models.CharField(max_length=20, default=STATUS_NOT_CONNECTED)
    webhook_enabled = models.BooleanField(default=False)
    send_enabled = models.BooleanField("店舗設定としての送信許可", default=False)
    official_account_name = models.CharField(max_length=80, blank=True)
    last_verified_at = models.DateTimeField(null=True, blank=True)
    note = models.CharField(max_length=300, blank=True)

    def has_credentials(self) -> bool:
        return bool(self.channel_id and self.channel_secret and self.channel_access_token)


class ConsentStatus(models.TextChoices):
    UNKNOWN = "unknown", "未確認"
    OPTED_IN = "opted_in", "配信同意"
    OPTED_OUT = "opted_out", "配信停止"


class LineUser(TimestampedModel):
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="line_users")
    line_user_id = models.CharField(max_length=64)
    display_name = models.CharField(max_length=80, blank=True)
    consent = models.CharField(max_length=20, choices=ConsentStatus.choices, default=ConsentStatus.UNKNOWN)
    consent_changed_at = models.DateTimeField(null=True, blank=True)
    customer = models.ForeignKey("customers.Customer", null=True, blank=True, on_delete=models.SET_NULL, related_name="line_users")
    linked_at = models.DateTimeField(null=True, blank=True)
    linked_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    is_following = models.BooleanField(default=True)
    note = models.CharField(max_length=200, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["store", "line_user_id"], name="uniq_line_user_per_store")]


class MessageTemplate(TimestampedModel):
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="line_templates")
    name = models.CharField(max_length=60)
    kind = models.CharField(max_length=20, default="thanks")  # thanks / visit_guide / reservation / other
    body = models.TextField("本文（{name} などの置換可）")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["kind", "id"]


class MessageDraftStatus(models.TextChoices):
    DRAFT = "draft", "下書き"
    REVIEWED = "reviewed", "送信前確認済"
    SENT = "sent", "送信済"
    MOCK_SENT = "mock_sent", "モック送信（実送信なし）"
    FAILED = "failed", "送信失敗"
    BLOCKED = "blocked", "送信不可（未接続・同意なし等）"


class MessageDraft(TimestampedModel):
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="line_drafts")
    line_user = models.ForeignKey(LineUser, on_delete=models.PROTECT, related_name="drafts")
    template = models.ForeignKey(MessageTemplate, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    body = models.TextField()
    status = models.CharField(max_length=20, choices=MessageDraftStatus.choices, default=MessageDraftStatus.DRAFT)
    reservation = models.ForeignKey("customers.Reservation", null=True, blank=True, on_delete=models.SET_NULL, related_name="line_drafts")
    inquiry = models.ForeignKey("Inquiry", null=True, blank=True, on_delete=models.SET_NULL, related_name="drafts")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    sent_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    sent_at = models.DateTimeField(null=True, blank=True)
    result = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-id"]


class WebhookEvent(models.Model):
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="line_webhook_events")
    webhook_event_id = models.CharField(max_length=80)
    event_type = models.CharField(max_length=40)
    payload = models.JSONField(default=dict)
    received_at = models.DateTimeField(auto_now_add=True)
    processed = models.BooleanField(default=False)
    signature_valid = models.BooleanField(default=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["store", "webhook_event_id"], name="uniq_webhook_event_per_store")]
        ordering = ["-id"]


class Inquiry(TimestampedModel):
    """LINE からの問い合わせ（受信メッセージ）の対応状況。"""

    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="line_inquiries")
    line_user = models.ForeignKey(LineUser, on_delete=models.CASCADE, related_name="inquiries")
    text = models.TextField()
    status = models.CharField(max_length=20, default="open")  # open / handled
    handled_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    handled_at = models.DateTimeField(null=True, blank=True)
    reservation = models.ForeignKey("customers.Reservation", null=True, blank=True, on_delete=models.SET_NULL, related_name="inquiries")
    webhook_event = models.ForeignKey(WebhookEvent, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-id"]
