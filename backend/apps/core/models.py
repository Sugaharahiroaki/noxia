"""
基盤モデル: 契約会社(Company) → 店舗(Store)、ログインユーザー(User)、
会社所属(CompanyMembership)、店舗所属(StoreMembership)、監査ログ、冪等性記録。

店舗に属する業務データは、各アプリのモデルで必ず store 外部キーを持つ。
"""
from __future__ import annotations

from datetime import time
from decimal import Decimal

from django.contrib.auth.models import AbstractUser
from django.db import models


class TimestampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Company(TimestampedModel):
    """契約会社。複数の店舗を持つ。"""

    name = models.CharField("会社名", max_length=100)
    code = models.SlugField("会社コード", max_length=40, unique=True)
    is_active = models.BooleanField("有効", default=True)
    # 同一会社内での顧客情報共有を許可するか（既定: 不許可）
    customer_sharing_enabled = models.BooleanField("会社内の顧客共有を許可", default=False)
    is_demo = models.BooleanField("開発用データ", default=False)
    note = models.TextField("メモ", blank=True)

    class Meta:
        verbose_name = "会社"
        ordering = ["id"]

    def __str__(self) -> str:
        return self.name


class Store(TimestampedModel):
    """店舗。必ず会社に所属する。"""

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="stores")
    name = models.CharField("店舗名", max_length=100)
    code = models.SlugField("店舗コード", max_length=40)
    is_active = models.BooleanField("有効", default=True)
    is_demo = models.BooleanField("開発用データ", default=False)
    timezone = models.CharField(max_length=40, default="Asia/Tokyo")
    # 店舗内で一意な伝票番号・会計番号の採番カウンタ（行ロックで更新する）
    next_slip_number = models.PositiveIntegerField(default=1)
    next_checkout_number = models.PositiveIntegerField(default=1)
    next_po_number = models.PositiveIntegerField(default=1)
    # LINE Webhook 用のパス識別子（店舗別）
    webhook_token = models.CharField(max_length=64, unique=True, blank=True, null=True)

    class Meta:
        verbose_name = "店舗"
        ordering = ["company_id", "id"]
        constraints = [models.UniqueConstraint(fields=["company", "code"], name="uniq_store_code_per_company")]

    def __str__(self) -> str:
        return f"{self.company.name} / {self.name}"


class PriceBasis(models.TextChoices):
    PER_PERSON = "per_person", "１人単位"
    PER_GROUP = "per_group", "１組単位"


class ServiceChargeTarget(models.TextChoices):
    ALL = "all", "全明細"
    COURSE_ONLY = "course_only", "セット・延長・コース変更のみ"
    PRODUCTS_ONLY = "products_only", "商品のみ"


class ServiceChargeOrder(models.TextChoices):
    BEFORE_TAX = "before_tax", "税抜金額にかけ、サービス料にも消費税をかける"
    AFTER_TAX = "after_tax", "税込金額にかけ、サービス料には消費税をかけない"


class RoundingMode(models.TextChoices):
    FLOOR = "floor", "切り捨て"
    CEIL = "ceil", "切り上げ"
    HALF_UP = "half_up", "四捨五入"


class UnlimitedChangeMode(models.TextChoices):
    UNSET = "unset", "未設定（変更操作を拒否）"
    FIXED = "fixed", "固定額"
    DIFFERENCE = "difference", "無制限コース料金との差額"


class UnlimitedChangeAfterExtMode(models.TextChoices):
    UNSET = "unset", "未設定（変更操作を拒否）"
    FIXED = "fixed", "固定額"
    SAME_AS_CHANGE = "same_as_change", "通常の変更料金と同じ"
    DIFFERENCE = "difference", "無制限コース料金と支払済み（セット＋延長）との差額（負なら0）"


class AlarmMode(models.TextChoices):
    TEN = "10", "10分前のみ"
    FIVE = "5", "5分前のみ"
    BOTH = "both", "10分前と5分前の両方"


class BackConfirmTiming(models.TextChoices):
    ORDER = "order", "注文時"
    CHECKOUT = "checkout", "会計確定時"
    PAYMENT = "payment", "入金完了時"


class StoreSettings(TimestampedModel):
    """
    店舗ごとの営業ルール設定。未確定の業務ルールはここで設定する。
    seed_dev で投入する値は「仮設定」であり、営業開始前にオーナーが確定する。
    """

    store = models.OneToOneField(Store, on_delete=models.CASCADE, related_name="settings")
    settings_note = models.TextField("設定メモ（仮設定の明示など）", blank=True)

    # コース料金
    price_basis = models.CharField(max_length=20, choices=PriceBasis.choices, default=PriceBasis.PER_PERSON)
    prices_include_tax = models.BooleanField("価格は税込（内税）", default=True)
    default_tax_rate = models.DecimalField("標準税率(%)", max_digits=5, decimal_places=2, default=Decimal("10.00"))

    # サービス料
    service_charge_enabled = models.BooleanField(default=False)
    service_charge_rate = models.DecimalField("サービス料率(%)", max_digits=5, decimal_places=2, default=Decimal("0.00"))
    service_charge_target = models.CharField(max_length=20, choices=ServiceChargeTarget.choices, default=ServiceChargeTarget.ALL)
    service_charge_order = models.CharField(max_length=20, choices=ServiceChargeOrder.choices, default=ServiceChargeOrder.BEFORE_TAX)
    service_charge_after_discount = models.BooleanField("値引き後の金額にサービス料をかける", default=True)

    # 端数処理
    tax_rounding = models.CharField(max_length=10, choices=RoundingMode.choices, default=RoundingMode.FLOOR)
    service_charge_rounding = models.CharField(max_length=10, choices=RoundingMode.choices, default=RoundingMode.FLOOR)
    total_rounding_unit = models.PositiveIntegerField("合計の丸め単位(円)", default=1)
    total_rounding_mode = models.CharField(max_length=10, choices=RoundingMode.choices, default=RoundingMode.FLOOR)

    # 無制限への変更料金（未設定なら変更操作をエラーにする。0円扱いにしない）
    unlimited_change_mode = models.CharField(max_length=20, choices=UnlimitedChangeMode.choices, default=UnlimitedChangeMode.UNSET)
    unlimited_change_price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    unlimited_change_after_ext_mode = models.CharField(
        max_length=20, choices=UnlimitedChangeAfterExtMode.choices, default=UnlimitedChangeAfterExtMode.UNSET
    )
    unlimited_change_after_ext_price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    default_unlimited_course = models.ForeignKey(
        "catalog.Course", null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
        help_text="「無制限へ変更」で適用するコース（差額計算にも使う）",
    )

    # 指名・同伴（null = 未設定。未設定のまま登録しようとするとエラー）
    nomination_fee = models.DecimalField("指名料", max_digits=12, decimal_places=2, null=True, blank=True)
    douhan_fee = models.DecimalField("同伴料", max_digits=12, decimal_places=2, null=True, blank=True)

    # アラーム
    alarm_mode = models.CharField(max_length=10, choices=AlarmMode.choices, default=AlarmMode.BOTH)

    # 営業日
    business_day_switch_time = models.TimeField("営業日切替時刻", default=time(6, 0))
    default_opening_float = models.DecimalField("既定の釣銭準備金", max_digits=12, decimal_places=2, default=Decimal("0"))

    # 会計
    receivable_allowed = models.BooleanField("ツケ・未収を許可", default=False)

    # バック
    default_back_confirm_timing = models.CharField(max_length=20, choices=BackConfirmTiming.choices, default=BackConfirmTiming.CHECKOUT)

    # 日払い（お渡し）
    # False の間は源泉所得税を計算・差し引きしない（源泉徴収明細も作らない）。変更は経営者のみ（tax.decide）
    withholding_enabled = models.BooleanField("日払いで源泉所得税を差し引く", default=False)
    # お渡しのときにチェックして差し引く控除（備品代など）。[{"code","label","percent","default_on"}]、報酬総額の%
    payout_deductions = models.JSONField("日払いの控除項目", default=list, blank=True)

    # 領収書・経費登録（店舗タブレット）
    receipt_capture_enabled = models.BooleanField("店舗タブレットで領収書を登録できる", default=True)
    # 店長が自店舗の領収書を承認できるか（既定は不可。承認はオーナー・経理担当）
    manager_can_approve_expenses = models.BooleanField("店長が領収書を承認できる", default=False)
    manager_approve_limit = models.DecimalField("店長が承認できる1件の上限（null=制限なし）", max_digits=12, decimal_places=2, null=True, blank=True)

    class Meta:
        verbose_name = "店舗設定"

    def __str__(self) -> str:
        return f"settings({self.store_id})"


class User(AbstractUser):
    """ログインアカウント。従業員情報(staff.Employee)とは分離する。"""

    display_name = models.CharField("表示名", max_length=60, blank=True)
    # 運営者（サービス提供者）権限。会社オーナーとは明確に分ける。
    is_platform_operator = models.BooleanField("運営者権限", default=False)

    class Meta:
        verbose_name = "ユーザー"

    def __str__(self) -> str:
        return self.display_name or self.username


class CompanyRole(models.TextChoices):
    OWNER = "owner", "会社オーナー"
    ADMIN = "admin", "会社管理者"
    MEMBER = "member", "一般（店舗権限に従う）"


class StoreRole(models.TextChoices):
    MANAGER = "manager", "店舗管理者"
    STAFF = "staff", "スタッフ"
    CAST = "cast", "キャスト"


class CompanyMembership(TimestampedModel):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="company_memberships")
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="memberships")
    role = models.CharField(max_length=20, choices=CompanyRole.choices, default=CompanyRole.MEMBER)
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "company"], name="uniq_company_membership")]


class StoreMembership(TimestampedModel):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="store_memberships")
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="memberships")
    role = models.CharField(max_length=20, choices=StoreRole.choices, default=StoreRole.STAFF)
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "store"], name="uniq_store_membership")]
        indexes = [models.Index(fields=["store", "is_active"])]


class Invitation(TimestampedModel):
    """ユーザー招待。トークンで受諾し、会社所属と店舗配属を作成する。"""

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="invitations")
    email = models.EmailField(blank=True)
    display_name = models.CharField(max_length=60, blank=True)
    company_role = models.CharField(max_length=20, choices=CompanyRole.choices, default=CompanyRole.MEMBER)
    # [{"store_id": 1, "role": "staff"}, ...]
    store_roles = models.JSONField(default=list, blank=True)
    employee = models.ForeignKey("staff.Employee", null=True, blank=True, on_delete=models.SET_NULL, related_name="invitations")
    token = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField()
    accepted_at = models.DateTimeField(null=True, blank=True)
    accepted_user = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_by = models.ForeignKey(User, null=True, on_delete=models.SET_NULL, related_name="+")


class AuditLog(models.Model):
    """操作履歴。認証情報や不要な個人情報は書き込まない。"""

    company = models.ForeignKey(Company, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    store = models.ForeignKey(Store, null=True, blank=True, on_delete=models.SET_NULL, related_name="audit_logs")
    user = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    action = models.CharField(max_length=80)
    target_type = models.CharField(max_length=60, blank=True)
    target_id = models.CharField(max_length=40, blank=True)
    changes = models.JSONField(default=dict, blank=True)
    request_id = models.CharField(max_length=40, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["store", "created_at"]), models.Index(fields=["company", "created_at"])]
        ordering = ["-id"]

    # 追記専用: 一度書いた監査ログは更新・削除できない（管理画面も読み取り専用）
    def save(self, *args, **kwargs):
        if self.pk is not None and not kwargs.pop("_allow_update", False):
            raise ValueError("監査ログは変更できません。")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("監査ログは削除できません（保存期間の整理は prune_audit_log を使う）。")


class CompanySettings(TimestampedModel):
    """会社単位の設定（AI 読取の有効化と予算など）。無ければ既定値で動く。"""

    company = models.OneToOneField(Company, on_delete=models.CASCADE, related_name="settings")
    # 領収書の AI 読取。会社がオンにし、かつサーバーに接続部品の設定があるときだけ呼ぶ
    ai_enabled = models.BooleanField("領収書の AI 読取を使う", default=False)
    ai_monthly_budget_jpy = models.DecimalField("AI の月間予算（円。0 なら上限なし）", max_digits=10, decimal_places=0, default=Decimal("3000"))
    ai_provider = models.CharField("AI の接続先（空ならサーバー設定）", max_length=20, blank=True)
    note = models.TextField(blank=True)

    class Meta:
        verbose_name = "会社設定"


class BackgroundJob(TimestampedModel):
    """非同期ジョブ（DB キュー）。manage.py run_jobs が取り出して実行する。"""

    QUEUED, RUNNING, DONE, FAILED = "queued", "running", "done", "failed"
    STATUS_CHOICES = [(QUEUED, "待機"), (RUNNING, "実行中"), (DONE, "完了"), (FAILED, "失敗")]

    kind = models.CharField(max_length=60)
    payload = models.JSONField(default=dict, blank=True)
    company = models.ForeignKey(Company, null=True, blank=True, on_delete=models.CASCADE, related_name="+")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=QUEUED)
    run_after = models.DateTimeField()
    attempts = models.PositiveSmallIntegerField(default=0)
    max_attempts = models.PositiveSmallIntegerField(default=3)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    error = models.CharField(max_length=500, blank=True)
    result = models.JSONField(default=dict, blank=True)
    created_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        indexes = [models.Index(fields=["status", "run_after"]), models.Index(fields=["kind", "status"])]
        ordering = ["-id"]


class OperatorAccessLog(models.Model):
    """運営者が顧客企業のデータへアクセスした記録。"""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="+")
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="+")
    store = models.ForeignKey(Store, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    method = models.CharField(max_length=10)
    path = models.CharField(max_length=300)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["company", "created_at"])]


class IdempotencyRecord(models.Model):
    """
    二重登録防止。クライアントは更新系リクエストに Idempotency-Key を付ける。
    同じ (user, key) は最初の結果を返し、処理中なら 409 を返す。
    """

    STATE_IN_PROGRESS = "in_progress"
    STATE_DONE = "done"

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="+")
    store = models.ForeignKey(Store, null=True, blank=True, on_delete=models.CASCADE, related_name="+")
    key = models.CharField(max_length=80)
    method = models.CharField(max_length=10)
    path = models.CharField(max_length=300)
    request_hash = models.CharField(max_length=64)
    state = models.CharField(max_length=20, default=STATE_IN_PROGRESS)
    status_code = models.PositiveSmallIntegerField(null=True, blank=True)
    response_body = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "key"], name="uniq_idempotency_user_key")]
        indexes = [models.Index(fields=["created_at"])]
