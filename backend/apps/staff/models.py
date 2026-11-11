"""
従業員・配属・時給・バック設定・勤怠・支払い。

- Employee は会社に所属（1人1レコード）。店舗配属は EmployeeStoreAssignment で分ける。
- ログインアカウント(User)は任意で紐付ける（在籍情報とは分離）。
- 時給・バック設定は適用開始日付きの履歴として保存し、過去の確定済み集計は変えない。
"""
from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.db import models

from apps.core.models import BackConfirmTiming, Company, RoundingMode, Store, StoreRole, TimestampedModel


class Employee(TimestampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="employees")
    user = models.OneToOneField(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="employee")
    full_name = models.CharField("氏名", max_length=80)
    display_name = models.CharField("表示名（源氏名など）", max_length=60)
    hired_on = models.DateField("入店日", null=True, blank=True)
    left_on = models.DateField("退職日", null=True, blank=True)
    is_active = models.BooleanField("在籍中", default=True)
    note = models.TextField(blank=True)

    class Meta:
        ordering = ["company_id", "display_name", "id"]
        indexes = [models.Index(fields=["company", "is_active"])]

    def __str__(self) -> str:
        return self.display_name


class EmployeeStoreAssignment(TimestampedModel):
    """店舗配属。複数店舗勤務でも Employee は1つ。"""

    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="assignments")
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="employee_assignments")
    role = models.CharField(max_length=20, choices=StoreRole.choices, default=StoreRole.CAST)
    is_active = models.BooleanField(default=True)
    started_on = models.DateField(null=True, blank=True)
    ended_on = models.DateField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["employee", "store"], name="uniq_employee_store_assignment")]
        indexes = [models.Index(fields=["store", "is_active"])]


class WageSetting(TimestampedModel):
    """時給設定（店舗別・個人別・適用開始日付き）。"""

    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="wage_settings")
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="wage_settings")
    hourly_wage = models.DecimalField("時給", max_digits=10, decimal_places=2)
    effective_from = models.DateField("適用開始日")
    note = models.CharField(max_length=200, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-effective_from", "-id"]
        indexes = [models.Index(fields=["store", "employee", "effective_from"])]


class BackTargetType(models.TextChoices):
    ALL_PRODUCTS = "all_products", "全商品"
    CATEGORY_KIND = "category_kind", "カテゴリ種別（ドリンク／ボトル等）"
    CATEGORY = "category", "カテゴリ"
    PRODUCT = "product", "商品"
    NOMINATION = "nomination", "指名"
    DOUHAN = "douhan", "同伴"


class BackCalcType(models.TextChoices):
    FIXED = "fixed", "固定額"
    PERCENT = "percent", "割合(%)"


class BackBasis(models.TextChoices):
    TAX_INCLUDED = "tax_included", "税込金額"
    TAX_EXCLUDED = "tax_excluded", "税抜金額"


class BackRule(TimestampedModel):
    """
    バック設定。employee が null なら店舗共通ルール、指定ありなら個人別ルール。
    優先順位: 個人別 > 共通、対象の具体性: 商品 > カテゴリ > カテゴリ種別 > 全商品。
    """

    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="back_rules")
    employee = models.ForeignKey(Employee, null=True, blank=True, on_delete=models.CASCADE, related_name="back_rules")
    name = models.CharField(max_length=80)
    target_type = models.CharField(max_length=20, choices=BackTargetType.choices)
    category_kind = models.CharField(max_length=20, blank=True)
    category = models.ForeignKey("catalog.Category", null=True, blank=True, on_delete=models.CASCADE, related_name="+")
    product = models.ForeignKey("catalog.Product", null=True, blank=True, on_delete=models.CASCADE, related_name="+")
    calc_type = models.CharField(max_length=10, choices=BackCalcType.choices)
    value = models.DecimalField("固定額(円) または 割合(%)", max_digits=10, decimal_places=2)
    basis = models.CharField(max_length=20, choices=BackBasis.choices, default=BackBasis.TAX_INCLUDED)
    include_discount = models.BooleanField("値引きを反映する（伝票の値引き割合を按分）", default=True)
    include_service_charge = models.BooleanField("サービス料を含める", default=False)
    rounding = models.CharField(max_length=10, choices=RoundingMode.choices, default=RoundingMode.FLOOR)
    confirm_timing = models.CharField(max_length=20, choices=BackConfirmTiming.choices, default=BackConfirmTiming.CHECKOUT)
    effective_from = models.DateField()
    effective_to = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    note = models.CharField(max_length=200, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["store_id", "-effective_from", "-id"]
        indexes = [models.Index(fields=["store", "is_active", "effective_from"])]

    def specificity(self) -> int:
        return {
            BackTargetType.PRODUCT: 4,
            BackTargetType.CATEGORY: 3,
            BackTargetType.CATEGORY_KIND: 2,
            BackTargetType.ALL_PRODUCTS: 1,
            BackTargetType.NOMINATION: 4,
            BackTargetType.DOUHAN: 4,
        }.get(self.target_type, 0)

    def snapshot(self) -> dict:
        return {
            "rule_id": self.id,
            "name": self.name,
            "employee_specific": self.employee_id is not None,
            "target_type": self.target_type,
            "category_kind": self.category_kind,
            "category_id": self.category_id,
            "product_id": self.product_id,
            "calc_type": self.calc_type,
            "value": str(self.value),
            "basis": self.basis,
            "include_discount": self.include_discount,
            "include_service_charge": self.include_service_charge,
            "rounding": self.rounding,
            "confirm_timing": self.confirm_timing,
            "effective_from": self.effective_from.isoformat(),
        }


class BackEntryStatus(models.TextChoices):
    PENDING = "pending", "未確定"
    CONFIRMED = "confirmed", "確定"
    REVERSED = "reversed", "取消済"


class BackEntry(models.Model):
    """
    バック発生の記録。計算条件をスナップショットで保存し、設定変更の影響を受けない。
    取消は打消しエントリ（amount が負、reversal_of に元を設定）で履歴を残す。
    """

    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="back_entries")
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="back_entries")
    visit = models.ForeignKey("sales.Visit", null=True, blank=True, on_delete=models.SET_NULL, related_name="back_entries")
    order_line = models.ForeignKey("sales.OrderLine", null=True, blank=True, on_delete=models.SET_NULL, related_name="back_entries")
    checkout = models.ForeignKey("sales.Checkout", null=True, blank=True, on_delete=models.SET_NULL, related_name="back_entries")
    kind = models.CharField(max_length=20, default="product")  # product / nomination / douhan / adjustment
    business_date = models.DateField()
    basis_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
    share_percent = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal("100"))
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    rule = models.ForeignKey(BackRule, null=True, blank=True, on_delete=models.SET_NULL, related_name="entries")
    rule_snapshot = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=20, choices=BackEntryStatus.choices, default=BackEntryStatus.PENDING)
    confirm_timing = models.CharField(max_length=20, default=BackConfirmTiming.CHECKOUT)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    reversal_of = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="reversals")
    reason = models.CharField(max_length=200, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-id"]
        indexes = [
            models.Index(fields=["store", "business_date"]),
            models.Index(fields=["store", "employee", "business_date"]),
            models.Index(fields=["store", "status"]),
        ]


class Shift(TimestampedModel):
    """勤怠（出勤〜退勤）。日付をまたぐ勤務も business_date で営業日に紐付ける。"""

    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="shifts")
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="shifts")
    business_date = models.DateField()
    clock_in = models.DateTimeField()
    clock_out = models.DateTimeField(null=True, blank=True)
    note = models.CharField(max_length=200, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-clock_in"]
        indexes = [models.Index(fields=["store", "business_date"]), models.Index(fields=["store", "employee", "business_date"])]

    @property
    def is_open(self) -> bool:
        return self.clock_out is None


class ShiftBreak(models.Model):
    shift = models.ForeignKey(Shift, on_delete=models.CASCADE, related_name="breaks")
    started_at = models.DateTimeField()
    ended_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["started_at"]


class ShiftCorrection(models.Model):
    """勤怠の修正履歴。"""

    shift = models.ForeignKey(Shift, on_delete=models.CASCADE, related_name="corrections")
    before = models.JSONField(default=dict)
    after = models.JSONField(default=dict)
    reason = models.CharField(max_length=200)
    corrected_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    corrected_at = models.DateTimeField(auto_now_add=True)


class EmployeePaymentKind(models.TextChoices):
    DAILY = "daily", "日払い"
    ADVANCE = "advance", "前払い"
    SETTLEMENT = "settlement", "締め支払い（残額）"
    OTHER = "other", "その他"


class EmployeePayment(TimestampedModel):
    """
    従業員への実際の支払い記録。支給対象額（計算値）とは区別する。
    レジ現金から支払った場合は cash_movement に関連付け、現金出金と二重に控除しない。
    amount は実際に渡した額（差引支給額）。この支払いで精算した報酬 = amount + withholding_amount + deduction_amount。
    源泉徴収の計算明細は accounting.WithholdingRecord（payment と1対1）に残す。
    """

    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="payments")
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="employee_payments")
    kind = models.CharField(max_length=20, choices=EmployeePaymentKind.choices, default=EmployeePaymentKind.DAILY)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    withholding_amount = models.DecimalField("源泉所得税（預り金）", max_digits=12, decimal_places=2, default=Decimal("0"))
    deduction_amount = models.DecimalField("その他控除（厚生費・備品代など）", max_digits=12, decimal_places=2, default=Decimal("0"))
    # この支払いで差し引いた控除の内訳 [{"code","label","calc","value","amount"}]。同じ勤務日の2回目以降の支払いで二重に引かないために使う
    deduction_items = models.JSONField(default=list, blank=True)
    method = models.CharField(max_length=20, default="cash")  # cash(レジ現金) / bank / other
    paid_at = models.DateTimeField()
    business_date = models.DateField()  # 支払った（レジから出した）営業日
    # どの営業日の勤務分か（日払い）。その日のお渡し額と突き合わせて二重払いを防ぐ
    work_date = models.DateField(null=True, blank=True)
    pay_period = models.ForeignKey("PayPeriod", null=True, blank=True, on_delete=models.SET_NULL, related_name="payments")
    cash_movement = models.OneToOneField("cash.CashMovement", null=True, blank=True, on_delete=models.SET_NULL, related_name="employee_payment")
    is_voided = models.BooleanField(default=False)
    void_reason = models.CharField(max_length=200, blank=True)
    note = models.CharField(max_length=200, blank=True)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-paid_at"]
        indexes = [
            models.Index(fields=["store", "business_date"]),
            models.Index(fields=["store", "employee", "business_date"]),
            models.Index(fields=["store", "employee", "work_date"]),
        ]


class PayPeriod(TimestampedModel):
    """締め期間（締め日・支払日）。締め後は明細を固定する。"""

    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="pay_periods")
    start_date = models.DateField()
    end_date = models.DateField()
    pay_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, default="open")  # open / closed
    closed_at = models.DateTimeField(null=True, blank=True)
    closed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    note = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["-start_date"]
        constraints = [models.UniqueConstraint(fields=["store", "start_date", "end_date"], name="uniq_pay_period_range")]


class PayStatement(models.Model):
    """締め時点の個人別明細スナップショット（支給対象額・実支払額・残額）。"""

    pay_period = models.ForeignKey(PayPeriod, on_delete=models.CASCADE, related_name="statements")
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="pay_statements")
    worked_minutes = models.PositiveIntegerField(default=0)
    wage_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
    back_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
    gross_amount = models.DecimalField("支給対象額", max_digits=12, decimal_places=2, default=Decimal("0"))
    paid_amount = models.DecimalField("期間内の実支払額", max_digits=12, decimal_places=2, default=Decimal("0"))
    balance_amount = models.DecimalField("残額", max_digits=12, decimal_places=2, default=Decimal("0"))
    details = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["pay_period", "employee"], name="uniq_statement_per_employee")]
