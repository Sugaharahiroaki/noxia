"""
営業日・現金管理・日締め。

理論現金 = 開始時釣銭 + 現金売上 + その他現金入金（未収回収の現金を含む） - 現金返金 - 現金支出 - 日払い - 途中回収
入金用取り分け額 = 実際の現金 - 翌日釣銭準備金（負なら不足額として表示）
"""
from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.db import models

from apps.core.models import Store, TimestampedModel


class BusinessDayStatus(models.TextChoices):
    OPEN = "open", "営業中"
    CLOSED = "closed", "締め済"


class BusinessDay(TimestampedModel):
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="business_days")
    date = models.DateField("営業日")
    status = models.CharField(max_length=10, choices=BusinessDayStatus.choices, default=BusinessDayStatus.OPEN)
    opened_at = models.DateTimeField()
    opened_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    opening_float = models.DecimalField("開始時釣銭準備金", max_digits=12, decimal_places=2, default=Decimal("0"))
    opening_float_carried = models.BooleanField("前日からの引継ぎ", default=False)

    # 締め時の集計スナップショット
    closed_at = models.DateTimeField(null=True, blank=True)
    closed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    cash_sales = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
    card_sales = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
    receivable_sales = models.DecimalField("未収として計上した額", max_digits=12, decimal_places=2, default=Decimal("0"))
    receivable_collected_cash = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
    receivable_collected_card = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
    cash_refunds = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
    card_refunds = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
    cash_in_other = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
    cash_out_expense = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
    cash_out_daily_pay = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
    cash_out_other = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
    cash_out_mid_collection = models.DecimalField("途中回収", max_digits=12, decimal_places=2, default=Decimal("0"))
    theoretical_cash = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
    counted_cash = models.DecimalField("実際の現金", max_digits=12, decimal_places=2, null=True, blank=True)
    denominations = models.JSONField(default=dict, blank=True)  # {"10000": 3, "5000": 1, ...}
    difference = models.DecimalField("差額（実際－理論）", max_digits=12, decimal_places=2, null=True, blank=True)
    difference_reason = models.CharField(max_length=300, blank=True)
    next_day_float = models.DecimalField("翌日釣銭準備金", max_digits=12, decimal_places=2, null=True, blank=True)
    set_aside_amount = models.DecimalField("入金用取り分け額", max_digits=12, decimal_places=2, null=True, blank=True)
    shortage_amount = models.DecimalField("不足・補充必要額", max_digits=12, decimal_places=2, null=True, blank=True)
    note = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["-date"]
        constraints = [models.UniqueConstraint(fields=["store", "date"], name="uniq_business_day_per_store")]
        indexes = [models.Index(fields=["store", "status"])]

    def __str__(self) -> str:
        return f"{self.store_id}:{self.date}"


class CashMovementKind(models.TextChoices):
    EXPENSE = "expense", "現金経費"
    DAILY_PAY = "daily_pay", "日払い・前払い"
    OTHER_IN = "other_in", "その他入金"
    OTHER_OUT = "other_out", "その他出金"
    MID_COLLECTION = "mid_collection", "途中回収"
    FLOAT_ADJUST_IN = "float_adjust_in", "釣銭補充"


class CashMovement(TimestampedModel):
    """レジ現金の入出金（売上・返金は別テーブルから集計するため含めない）。"""

    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="cash_movements")
    business_day = models.ForeignKey(BusinessDay, on_delete=models.PROTECT, related_name="movements")
    kind = models.CharField(max_length=20, choices=CashMovementKind.choices)
    amount = models.DecimalField("金額（正の値）", max_digits=12, decimal_places=2)
    reason = models.CharField(max_length=200)
    employee = models.ForeignKey("staff.Employee", null=True, blank=True, on_delete=models.SET_NULL, related_name="cash_movements")
    occurred_at = models.DateTimeField()
    performed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    is_voided = models.BooleanField(default=False)
    void_reason = models.CharField(max_length=200, blank=True)
    voided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    # 現金経費の勘定科目（承認済みの領収書から設定。未設定なら仕訳では雑費＋警告）
    account = models.ForeignKey("accounting.Account", null=True, blank=True, on_delete=models.SET_NULL, related_name="cash_movements")

    class Meta:
        ordering = ["-occurred_at"]
        indexes = [models.Index(fields=["store", "business_day", "kind"])]

    @property
    def is_inflow(self) -> bool:
        return self.kind in (CashMovementKind.OTHER_IN, CashMovementKind.FLOAT_ADJUST_IN)


class BankDeposit(TimestampedModel):
    """銀行入金。売上とは別の記録（重複計上しない）。"""

    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="bank_deposits")
    business_day = models.ForeignKey(BusinessDay, on_delete=models.PROTECT, related_name="bank_deposits")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    deposited_at = models.DateTimeField()
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    note = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["-deposited_at"]


class ClosingRevision(models.Model):
    """締め後の修正履歴。"""

    business_day = models.ForeignKey(BusinessDay, on_delete=models.CASCADE, related_name="revisions")
    changed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    changed_at = models.DateTimeField(auto_now_add=True)
    reason = models.CharField(max_length=300)
    before = models.JSONField(default=dict)
    after = models.JSONField(default=dict)

    class Meta:
        ordering = ["-id"]
