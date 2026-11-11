"""
年間決算: 期首・期末棚卸、固定資産（減価償却・開業費）、決算整理。

- 棚卸・減価償却・決算整理は、確定・計算のときに決算整理仕訳（accounting.Journal, entry_kind=adjustment）を作る。
  仕訳は source_type / source_id で一意なので、計算し直しても二重にならない（内容が変われば revision が上がる）。
- 償却率はコードで 1/耐用年数 を計算せず、data/depreciation_rates.json（国税庁の償却率表を転記）の値を資産に保存する。
- 締めた年度（FiscalYear.is_locked）では作成・再計算できない。
"""
from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.db import models

from apps.core.models import Store, TimestampedModel


class InventoryKind(models.TextChoices):
    OPENING = "opening", "期首"
    CLOSING = "closing", "期末"


class InventoryMethod(models.TextChoices):
    LAST_PURCHASE_COST = "last_purchase_cost", "最終仕入原価法（届出がない場合の法定評価方法）"
    MANUAL = "manual", "手入力"


class InventoryClosing(TimestampedModel):
    """期首・期末の商品棚卸（店舗ごと）。確定で決算整理仕訳を作る。"""

    taxpayer = models.ForeignKey("accounting.TaxPayer", on_delete=models.CASCADE, related_name="inventory_closings")
    fiscal_year = models.ForeignKey("accounting.FiscalYear", on_delete=models.CASCADE, related_name="inventory_closings")
    store = models.ForeignKey(Store, on_delete=models.PROTECT, related_name="inventory_closings")
    kind = models.CharField(max_length=10, choices=InventoryKind.choices)
    as_of = models.DateField("棚卸日")
    method = models.CharField(max_length=20, choices=InventoryMethod.choices, default=InventoryMethod.LAST_PURCHASE_COST)
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
    stock_take = models.ForeignKey("inventory.StockTake", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    status = models.CharField(max_length=10, default="draft")  # draft / confirmed
    confirmed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    confirmed_at = models.DateTimeField(null=True, blank=True)
    journal = models.ForeignKey("accounting.Journal", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    note = models.CharField(max_length=300, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        verbose_name = verbose_name_plural = "棚卸"
        ordering = ["-fiscal_year__year", "store_id", "kind"]
        constraints = [models.UniqueConstraint(fields=["fiscal_year", "store", "kind"], name="uniq_inventory_closing")]


class InventoryClosingLine(models.Model):
    closing = models.ForeignKey(InventoryClosing, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey("catalog.Product", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    name = models.CharField(max_length=120)
    quantity = models.DecimalField(max_digits=12, decimal_places=4, default=Decimal("0"))
    unit_cost = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
    amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
    cost_source = models.CharField(max_length=20, default="manual")  # supplier_product / product_cost / manual / none

    class Meta:
        ordering = ["id"]


class FixedAssetKind(models.TextChoices):
    TANGIBLE = "tangible", "有形固定資産（器具備品・車両など）"
    INTANGIBLE = "intangible", "無形固定資産（ソフトウェアなど）"
    DEFERRED_OPENING_COST = "deferred_opening_cost", "開業費（繰延資産）"
    DEFERRED_OTHER = "deferred_other", "その他の繰延資産"


class DepreciationMethod(models.TextChoices):
    STRAIGHT_LINE = "straight_line", "定額法（個人の法定償却方法）"
    DECLINING_BALANCE = "declining_balance", "定率法（届出が必要・未対応）"
    LUMP_SUM_3YR = "lump_sum_3yr", "一括償却資産（20万円未満・3年で均等）"
    IMMEDIATE = "immediate", "少額（10万円未満・全額その年の経費）"
    BLUE_SMALL_ASSET = "blue_small_asset", "30万円未満の特例（青色申告者のみ・年300万円まで）"
    VOLUNTARY = "voluntary", "任意償却（開業費など繰延資産）"


class FixedAssetStatus(models.TextChoices):
    IN_USE = "in_use", "使用中"
    DISPOSED = "disposed", "除却"
    SOLD = "sold", "売却"


class FixedAsset(TimestampedModel):
    """固定資産・開業費。取得価額・償却方法・償却率（表の値）・事業専用割合を持つ。"""

    taxpayer = models.ForeignKey("accounting.TaxPayer", on_delete=models.CASCADE, related_name="fixed_assets")
    store = models.ForeignKey(Store, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    name = models.CharField("資産名", max_length=120)
    kind = models.CharField(max_length=30, choices=FixedAssetKind.choices, default=FixedAssetKind.TANGIBLE)
    category = models.CharField("種類（器具備品・建物附属設備・車両運搬具・ソフトウェア…）", max_length=60, blank=True)
    acquired_on = models.DateField("取得日（開業費は開業日）")
    acquisition_cost = models.DecimalField("取得価額", max_digits=12, decimal_places=2)
    business_use_percent = models.DecimalField("事業専用割合(%)", max_digits=5, decimal_places=2, default=Decimal("100"))
    depreciation_method = models.CharField(max_length=20, choices=DepreciationMethod.choices, default=DepreciationMethod.STRAIGHT_LINE)
    useful_life_years = models.PositiveSmallIntegerField("耐用年数", null=True, blank=True)
    rate = models.DecimalField("償却率（国税庁の表の値）", max_digits=6, decimal_places=4, null=True, blank=True)
    memorandum_value = models.DecimalField("備忘価額", max_digits=12, decimal_places=2, default=Decimal("1"))
    status = models.CharField(max_length=10, choices=FixedAssetStatus.choices, default=FixedAssetStatus.IN_USE)
    disposed_on = models.DateField(null=True, blank=True)
    disposal_amount = models.DecimalField("売却額", max_digits=12, decimal_places=2, null=True, blank=True)
    # 取得時の支払いを仕訳するか（none: 既に経費・仕訳として記録済み／別経路で記録するので何もしない）
    paid_from = models.CharField(max_length=20, default="none")  # none / bank / cash / owner_personal / card
    evidence = models.ForeignKey("accounting.Evidence", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    acquisition_journal = models.ForeignKey("accounting.Journal", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    note = models.CharField(max_length=300, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        verbose_name = verbose_name_plural = "固定資産"
        ordering = ["taxpayer_id", "acquired_on", "id"]

    def __str__(self) -> str:
        return self.name

    @property
    def is_deferred(self) -> bool:
        return self.kind in (FixedAssetKind.DEFERRED_OPENING_COST, FixedAssetKind.DEFERRED_OTHER)

    @property
    def asset_account_key(self) -> str:
        return "deferred_opening_cost" if self.is_deferred else "equipment"

    @property
    def expense_account_key(self) -> str:
        if self.depreciation_method == DepreciationMethod.IMMEDIATE:
            return "supplies"
        return "opening_cost_amort" if self.is_deferred else "depreciation"


class DepreciationEntry(TimestampedModel):
    """年度ごとの償却額。計算し直すと同じ行を上書きする（journal は source_id で一意）。"""

    asset = models.ForeignKey(FixedAsset, on_delete=models.CASCADE, related_name="entries")
    fiscal_year = models.ForeignKey("accounting.FiscalYear", on_delete=models.CASCADE, related_name="depreciation_entries")
    months_in_service = models.PositiveSmallIntegerField(default=12)
    amount_total = models.DecimalField("償却費（按分前）", max_digits=12, decimal_places=2, default=Decimal("0"))
    amount_business = models.DecimalField("必要経費算入額", max_digits=12, decimal_places=2, default=Decimal("0"))
    amount_household = models.DecimalField("家事分（事業主貸）", max_digits=12, decimal_places=2, default=Decimal("0"))
    book_value_begin = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
    book_value_end = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
    requested_amount = models.DecimalField("任意償却で指定した額", max_digits=12, decimal_places=2, null=True, blank=True)
    calc_basis = models.JSONField(default=dict, blank=True)
    journal = models.ForeignKey("accounting.Journal", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        verbose_name = verbose_name_plural = "減価償却"
        ordering = ["fiscal_year__year", "asset_id"]
        constraints = [models.UniqueConstraint(fields=["asset", "fiscal_year"], name="uniq_depreciation_entry")]


class AdjustmentKind(models.TextChoices):
    ACCRUED_EXPENSE = "accrued_expense", "未払金・未払費用（今年の費用で支払いは翌年）"
    ACCRUED_REVENUE = "accrued_revenue", "未収入金（今年の収入で入金は翌年）"
    PREPAID = "prepaid", "前払費用（翌年分の支払い）"
    HOUSEHOLD_SPLIT = "household_split", "家事按分（事業と家事の共用分を除く）"
    BAD_DEBT = "bad_debt", "貸倒（回収できないツケ）"
    INVENTORY = "inventory", "棚卸"
    DEPRECIATION = "depreciation", "減価償却"
    OPENING_COST = "opening_cost", "開業費"
    OTHER = "other", "その他"


class ClosingAdjustment(TimestampedModel):
    """決算整理（手入力）。借方・貸方の科目を選び、決算整理仕訳を作る。訂正は取消＋新規。"""

    taxpayer = models.ForeignKey("accounting.TaxPayer", on_delete=models.CASCADE, related_name="closing_adjustments")
    fiscal_year = models.ForeignKey("accounting.FiscalYear", on_delete=models.CASCADE, related_name="closing_adjustments")
    store = models.ForeignKey(Store, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    kind = models.CharField(max_length=20, choices=AdjustmentKind.choices)
    issue_date = models.DateField()
    description = models.CharField(max_length=200)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    debit_account = models.ForeignKey("accounting.Account", on_delete=models.PROTECT, related_name="+")
    credit_account = models.ForeignKey("accounting.Account", on_delete=models.PROTECT, related_name="+")
    journal = models.ForeignKey("accounting.Journal", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    is_voided = models.BooleanField(default=False)
    void_reason = models.CharField(max_length=200, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        verbose_name = verbose_name_plural = "決算整理"
        ordering = ["-issue_date", "-id"]
