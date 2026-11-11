"""
仕入先・在庫・棚卸し・発注。

- 在庫は StockMovement の台帳から算出する（単位: 本。ショット販売は 1杯量/容量 の理論消費で本数換算）。
- 棚卸しで実在庫を入力し、理論在庫との差を調整として記録する。推定値を実測値として表示しない。
- 発注は 下書き → 確認(オーナー) → 発注済み → 入荷済み。外部への自動送信は行わない。
"""
from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.db import models

from apps.core.models import Store, TimestampedModel


class Supplier(TimestampedModel):
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="suppliers")
    name = models.CharField("仕入先名", max_length=100)
    contact = models.CharField("連絡先", max_length=120, blank=True)
    order_method = models.CharField("発注方法メモ（電話・FAX・LINE等）", max_length=120, blank=True)
    is_active = models.BooleanField(default=True)
    note = models.TextField(blank=True)

    class Meta:
        ordering = ["name", "id"]

    def __str__(self) -> str:
        return self.name


class SupplierProduct(TimestampedModel):
    """仕入先ごとの商品・仕入単価・発注単位。"""

    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="supplier_products")
    supplier = models.ForeignKey(Supplier, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey("catalog.Product", on_delete=models.CASCADE, related_name="supplier_items")
    purchase_price = models.DecimalField("仕入単価（発注単位あたり）", max_digits=12, decimal_places=2)
    order_unit_name = models.CharField("発注単位名（ケース等）", max_length=30, default="本")
    units_per_order_unit = models.DecimalField("発注単位あたりの入荷本数", max_digits=8, decimal_places=2, default=Decimal("1"))
    lead_days = models.PositiveSmallIntegerField(default=0)
    is_preferred = models.BooleanField(default=False)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["supplier", "product"], name="uniq_supplier_product")]


class StockMovementKind(models.TextChoices):
    RECEIVE = "receive", "入荷"
    SALE = "sale", "販売"
    SALE_REVERSAL = "sale_reversal", "販売取消"
    WASTE = "waste", "廃棄"
    ADJUST = "adjust", "棚卸調整"
    KEEP_OUT = "keep_out", "キープ移管（販売在庫→キープ）"


class StockMovement(models.Model):
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="stock_movements")
    product = models.ForeignKey("catalog.Product", on_delete=models.CASCADE, related_name="stock_movements")
    kind = models.CharField(max_length=20, choices=StockMovementKind.choices)
    quantity = models.DecimalField("増減（本。販売・廃棄は負）", max_digits=12, decimal_places=4)
    is_theoretical = models.BooleanField("理論消費（ショット換算）", default=False)
    order_line = models.ForeignKey("sales.OrderLine", null=True, blank=True, on_delete=models.SET_NULL, related_name="stock_movements")
    reference = models.CharField(max_length=80, blank=True)
    reason = models.CharField(max_length=200, blank=True)
    occurred_at = models.DateTimeField()
    performed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-occurred_at", "-id"]
        indexes = [models.Index(fields=["store", "product", "occurred_at"])]


class StockTake(TimestampedModel):
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="stock_takes")
    taken_at = models.DateTimeField()
    status = models.CharField(max_length=20, default="draft")  # draft / applied
    applied_at = models.DateTimeField(null=True, blank=True)
    performed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    note = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["-taken_at"]


class StockTakeLine(models.Model):
    stock_take = models.ForeignKey(StockTake, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey("catalog.Product", on_delete=models.CASCADE, related_name="+")
    theoretical_qty = models.DecimalField(max_digits=12, decimal_places=4)
    counted_full_bottles = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0"))
    counted_open_ml = models.DecimalField("開封ボトルの残量(ml)合計", max_digits=10, decimal_places=1, default=Decimal("0"))
    counted_qty = models.DecimalField("実在庫（本換算）", max_digits=12, decimal_places=4)
    difference = models.DecimalField(max_digits=12, decimal_places=4)
    note = models.CharField(max_length=200, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["stock_take", "product"], name="uniq_stocktake_product")]


class PurchaseOrderStatus(models.TextChoices):
    DRAFT = "draft", "下書き"
    CONFIRMED = "confirmed", "確認済（オーナー確認）"
    ORDERED = "ordered", "発注済（仕入先へ連絡済）"
    PARTIAL = "partially_received", "一部入荷"
    RECEIVED = "received", "入荷済"
    CANCELLED = "cancelled", "取消"


class PurchaseOrder(TimestampedModel):
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="purchase_orders")
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name="purchase_orders")
    number = models.PositiveIntegerField("発注番号")
    status = models.CharField(max_length=20, choices=PurchaseOrderStatus.choices, default=PurchaseOrderStatus.DRAFT)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    confirmed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    confirmed_at = models.DateTimeField(null=True, blank=True)
    ordered_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    ordered_at = models.DateTimeField(null=True, blank=True)
    ordered_via = models.CharField("発注手段（電話・FAX等、手動で連絡した方法）", max_length=60, blank=True)
    expected_on = models.DateField(null=True, blank=True)
    note = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["-id"]
        constraints = [models.UniqueConstraint(fields=["store", "number"], name="uniq_po_number_per_store")]
        indexes = [models.Index(fields=["store", "status"])]


class PurchaseOrderLine(models.Model):
    purchase_order = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey("catalog.Product", on_delete=models.PROTECT, related_name="+")
    supplier_product = models.ForeignKey(SupplierProduct, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    order_units = models.DecimalField("発注数（発注単位）", max_digits=10, decimal_places=2)
    units_per_order_unit = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal("1"))
    unit_price = models.DecimalField("仕入単価（発注単位あたり）", max_digits=12, decimal_places=2, default=Decimal("0"))
    received_bottles = models.DecimalField("入荷済み本数", max_digits=12, decimal_places=2, default=Decimal("0"))
    note = models.CharField(max_length=200, blank=True)

    @property
    def expected_bottles(self) -> Decimal:
        return self.order_units * self.units_per_order_unit


class GoodsReceipt(TimestampedModel):
    purchase_order = models.ForeignKey(PurchaseOrder, on_delete=models.PROTECT, related_name="receipts")
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="goods_receipts")
    received_at = models.DateTimeField()
    received_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    note = models.CharField(max_length=200, blank=True)


class GoodsReceiptLine(models.Model):
    receipt = models.ForeignKey(GoodsReceipt, on_delete=models.CASCADE, related_name="lines")
    po_line = models.ForeignKey(PurchaseOrderLine, on_delete=models.PROTECT, related_name="receipt_lines")
    bottles = models.DecimalField(max_digits=12, decimal_places=2)
