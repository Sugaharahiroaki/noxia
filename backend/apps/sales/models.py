"""
営業データ: 伝票(Visit)・コース適用(CourseEntry)・明細(OrderLine)・会計(Checkout)・支払い(Payment)・
返金(Refund)・未収(Receivable)。

金額は取引時点の情報（価格・税率・税込区分）を明細にスナップショットする。
会計済みの伝票・明細は通常操作で直接書き換えない（取消は打消し・履歴で表現する）。
"""
from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.db import models

from apps.core.models import Store, TimestampedModel


class VisitStatus(models.TextChoices):
    OPEN = "open", "営業中"
    CLOSED = "closed", "会計済"
    CANCELLED = "cancelled", "取消"


class Visit(TimestampedModel):
    """来店（伝票）。卓に紐付き、複数のコース適用（人・グループ単位）を持つ。"""

    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="visits")
    business_date = models.DateField()
    slip_number = models.PositiveIntegerField("伝票番号")
    table = models.ForeignKey("catalog.Table", on_delete=models.PROTECT, related_name="visits")
    status = models.CharField(max_length=20, choices=VisitStatus.choices, default=VisitStatus.OPEN)
    opened_at = models.DateTimeField()
    closed_at = models.DateTimeField(null=True, blank=True)
    guest_count = models.PositiveSmallIntegerField(default=1)
    customer = models.ForeignKey("customers.Customer", null=True, blank=True, on_delete=models.SET_NULL, related_name="visits")
    customer_name = models.CharField("顧客名（任意）", max_length=80, blank=True)
    primary_employee = models.ForeignKey("staff.Employee", null=True, blank=True, on_delete=models.SET_NULL, related_name="primary_visits")
    note = models.CharField(max_length=300, blank=True)
    cancel_reason = models.CharField(max_length=200, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-opened_at"]
        constraints = [models.UniqueConstraint(fields=["store", "slip_number"], name="uniq_slip_number_per_store")]
        indexes = [
            models.Index(fields=["store", "status"]),
            models.Index(fields=["store", "business_date"]),
            models.Index(fields=["store", "table", "status"]),
        ]

    def __str__(self) -> str:
        return f"伝票#{self.slip_number}"


class CourseEntry(TimestampedModel):
    """
    伝票内の人またはグループ単位のコース適用。開始時刻・終了予定を個別に持つ。
    planned_end_at が null なら時間無制限（アラーム対象外）。
    """

    visit = models.ForeignKey(Visit, on_delete=models.CASCADE, related_name="course_entries")
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="course_entries")
    label = models.CharField("対象（例: 田中様 / グループA）", max_length=60, blank=True)
    guest_count = models.PositiveSmallIntegerField(default=1)
    course = models.ForeignKey("catalog.Course", on_delete=models.PROTECT, related_name="entries")
    course_name = models.CharField(max_length=80)
    course_price = models.DecimalField(max_digits=12, decimal_places=2)
    extension_price = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
    extension_minutes = models.PositiveIntegerField(default=60)
    started_at = models.DateTimeField()
    planned_end_at = models.DateTimeField(null=True, blank=True)
    is_unlimited = models.BooleanField(default=False)
    extension_count = models.PositiveSmallIntegerField(default=0)
    ended_at = models.DateTimeField(null=True, blank=True)
    employee = models.ForeignKey("staff.Employee", null=True, blank=True, on_delete=models.SET_NULL, related_name="course_entries")
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["id"]
        indexes = [models.Index(fields=["store", "is_active", "planned_end_at"])]


class CourseEvent(models.Model):
    """延長・無制限変更・席移動などの操作記録（誰がいつ）。"""

    course_entry = models.ForeignKey(CourseEntry, on_delete=models.CASCADE, related_name="events")
    kind = models.CharField(max_length=30)  # start / extend / change_unlimited / move_table / end
    performed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    performed_at = models.DateTimeField(auto_now_add=True)
    from_planned_end_at = models.DateTimeField(null=True, blank=True)
    to_planned_end_at = models.DateTimeField(null=True, blank=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    note = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["id"]


class LineType(models.TextChoices):
    COURSE_SET = "course_set", "セット"
    EXTENSION = "extension", "延長"
    COURSE_CHANGE = "course_change", "コース変更（無制限へ）"
    NOMINATION = "nomination", "指名"
    DOUHAN = "douhan", "同伴"
    PRODUCT = "product", "商品"
    DISCOUNT = "discount", "値引き"


class LineStatus(models.TextChoices):
    ACTIVE = "active", "有効"
    CANCELLED = "cancelled", "取消"


class OrderLine(TimestampedModel):
    """伝票明細。価格・税率は登録時点のスナップショット。"""

    visit = models.ForeignKey(Visit, on_delete=models.CASCADE, related_name="lines")
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="order_lines")
    line_type = models.CharField(max_length=20, choices=LineType.choices)
    course_entry = models.ForeignKey(CourseEntry, null=True, blank=True, on_delete=models.SET_NULL, related_name="lines")
    product = models.ForeignKey("catalog.Product", null=True, blank=True, on_delete=models.SET_NULL, related_name="order_lines")
    name = models.CharField(max_length=120)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    quantity = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal("1"))
    amount = models.DecimalField("金額（単価×数量。値引きは負）", max_digits=12, decimal_places=2)
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2)
    tax_included = models.BooleanField(default=True)
    category_kind = models.CharField(max_length=20, blank=True)
    category_id_snapshot = models.BigIntegerField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=LineStatus.choices, default=LineStatus.ACTIVE)
    cancel_reason = models.CharField(max_length=200, blank=True)
    cancelled_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    cancelled_at = models.DateTimeField(null=True, blank=True)
    discount_reason = models.CharField(max_length=200, blank=True)
    checkout = models.ForeignKey("Checkout", null=True, blank=True, on_delete=models.SET_NULL, related_name="lines")
    note = models.CharField(max_length=200, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["id"]
        indexes = [models.Index(fields=["store", "visit", "status"]), models.Index(fields=["store", "product"])]


class OrderLineBackTarget(models.Model):
    """明細のバック対象従業員（複数可）。share_type=percent なら合計100%を検証する。"""

    order_line = models.ForeignKey(OrderLine, on_delete=models.CASCADE, related_name="back_targets")
    employee = models.ForeignKey("staff.Employee", on_delete=models.PROTECT, related_name="+")
    share_type = models.CharField(max_length=10, default="percent")  # percent / fixed
    share_value = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("100"))

    class Meta:
        constraints = [models.UniqueConstraint(fields=["order_line", "employee"], name="uniq_back_target_per_line")]


class CheckoutStatus(models.TextChoices):
    CONFIRMED = "confirmed", "確定"
    VOIDED = "voided", "取消"


class Checkout(TimestampedModel):
    """
    会計。伝票全体(kind=full)または一部明細(kind=partial: 分割・個別会計)。
    金額内訳は確定時に計算して保存し、以後は再計算しない（手書き領収書の転記はこの値を参照する）。
    """

    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="checkouts")
    visit = models.ForeignKey(Visit, on_delete=models.PROTECT, related_name="checkouts")
    number = models.PositiveIntegerField("会計番号")
    kind = models.CharField(max_length=10, default="full")  # full / partial
    status = models.CharField(max_length=20, choices=CheckoutStatus.choices, default=CheckoutStatus.CONFIRMED)
    business_date = models.DateField()
    confirmed_at = models.DateTimeField()
    confirmed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    # 内訳スナップショット
    subtotal = models.DecimalField("明細合計（値引き前）", max_digits=12, decimal_places=2)
    discount_total = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
    service_charge = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
    tax_total = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
    tax_breakdown = models.JSONField(default=list, blank=True)  # [{rate, taxable_excl, tax, total_incl}]
    total_before_rounding = models.DecimalField(max_digits=12, decimal_places=2)
    rounding_adjustment = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
    total = models.DecimalField("請求額（税込）", max_digits=12, decimal_places=2)
    total_excl_tax = models.DecimalField("税抜金額", max_digits=12, decimal_places=2)
    breakdown = models.JSONField(default=dict, blank=True)  # セット/延長/商品/指名/同伴/値引き の内訳
    settings_snapshot = models.JSONField(default=dict, blank=True)
    void_reason = models.CharField(max_length=200, blank=True)
    voided_at = models.DateTimeField(null=True, blank=True)
    voided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    note = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["-confirmed_at"]
        constraints = [models.UniqueConstraint(fields=["store", "number"], name="uniq_checkout_number_per_store")]
        indexes = [models.Index(fields=["store", "business_date"]), models.Index(fields=["store", "status"])]

    @property
    def paid_total(self) -> Decimal:
        return sum((p.amount for p in self.payments.filter(status="valid")), Decimal("0"))


class PaymentMethod(models.TextChoices):
    CASH = "cash", "現金"
    CARD = "card", "カード"
    RECEIVABLE = "receivable", "ツケ・未収"


class Payment(TimestampedModel):
    """支払い記録。カードは既存端末で決済後にスタッフが記録する（カード番号は保存しない）。"""

    checkout = models.ForeignKey(Checkout, on_delete=models.CASCADE, related_name="payments")
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="payments")
    business_date = models.DateField()
    method = models.CharField(max_length=20, choices=PaymentMethod.choices)
    amount = models.DecimalField("充当額", max_digits=12, decimal_places=2)
    tendered = models.DecimalField("預り額（現金）", max_digits=12, decimal_places=2, null=True, blank=True)
    change_amount = models.DecimalField("釣銭", max_digits=12, decimal_places=2, default=Decimal("0"))
    paid_at = models.DateTimeField()
    status = models.CharField(max_length=20, default="valid")  # valid / refunded / voided
    card_reference = models.CharField("端末の伝票番号など（任意）", max_length=60, blank=True)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    note = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["id"]
        indexes = [models.Index(fields=["store", "business_date", "method"])]


class Refund(TimestampedModel):
    """返金（会計取消に伴う返金、または一部返金）。元の会計・支払いに関連付ける。"""

    checkout = models.ForeignKey(Checkout, on_delete=models.PROTECT, related_name="refunds")
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="refunds")
    payment = models.ForeignKey(Payment, null=True, blank=True, on_delete=models.SET_NULL, related_name="refunds")
    business_date = models.DateField()
    method = models.CharField(max_length=20, choices=PaymentMethod.choices)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    reason = models.CharField(max_length=200)
    refunded_at = models.DateTimeField()
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    # カードの場合、決済端末側の取消・返金はアプリとは別操作。端末側で実施したかをスタッフが記録する。
    terminal_refund_done = models.BooleanField(default=False)
    note = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["-refunded_at"]
        indexes = [models.Index(fields=["store", "business_date"])]


class Receivable(TimestampedModel):
    """ツケ・未収。回収時は売上として二重計上しない（ReceivableCollection で記録）。"""

    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="receivables")
    checkout = models.ForeignKey(Checkout, on_delete=models.PROTECT, related_name="receivables")
    payment = models.OneToOneField(Payment, on_delete=models.PROTECT, related_name="receivable")
    customer = models.ForeignKey("customers.Customer", null=True, blank=True, on_delete=models.SET_NULL, related_name="receivables")
    debtor_name = models.CharField(max_length=80, blank=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    collected_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
    status = models.CharField(max_length=20, default="open")  # open / collected / written_off
    business_date = models.DateField()
    note = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["-id"]
        indexes = [models.Index(fields=["store", "status"])]


class ReceivableCollection(TimestampedModel):
    receivable = models.ForeignKey(Receivable, on_delete=models.PROTECT, related_name="collections")
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="receivable_collections")
    business_date = models.DateField()
    method = models.CharField(max_length=20, default="cash")  # cash / card
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    collected_at = models.DateTimeField()
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    note = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["-collected_at"]
        indexes = [models.Index(fields=["store", "business_date"])]
