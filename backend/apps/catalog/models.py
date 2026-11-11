"""
商品・コース・卓（店舗ごとのマスタ）。
価格は取引時点で伝票明細にスナップショットされるため、ここを変更しても過去の金額は変わらない。
"""
from __future__ import annotations

from decimal import Decimal

from django.db import models

from apps.core.models import Store, TimestampedModel


class Course(TimestampedModel):
    """飲み放題などのセットコース。duration_minutes が null なら時間無制限。"""

    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="courses")
    name = models.CharField("コース名", max_length=80)
    price = models.DecimalField("料金", max_digits=12, decimal_places=2)
    duration_minutes = models.PositiveIntegerField("時間（分）", null=True, blank=True)
    extension_price = models.DecimalField("延長料金（1回あたり）", max_digits=12, decimal_places=2, default=Decimal("0"))
    extension_minutes = models.PositiveIntegerField("延長1回の分数", default=60)
    tax_rate = models.DecimalField("税率(%)", max_digits=5, decimal_places=2, default=Decimal("10.00"))
    is_active = models.BooleanField("販売中", default=True)
    sort_order = models.PositiveIntegerField(default=0)
    note = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["store_id", "sort_order", "id"]
        indexes = [models.Index(fields=["store", "is_active"])]

    @property
    def is_unlimited(self) -> bool:
        return self.duration_minutes is None

    def __str__(self) -> str:
        return self.name


class CategoryKind(models.TextChoices):
    DRINK = "drink", "ドリンク"
    CAST_DRINK = "cast_drink", "キャストドリンク"
    BOTTLE = "bottle", "ボトル"
    SHOT = "shot", "ショット"
    FOOD = "food", "フード"
    OTHER = "other", "その他"


class Category(TimestampedModel):
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="categories")
    name = models.CharField(max_length=60)
    kind = models.CharField(max_length=20, choices=CategoryKind.choices, default=CategoryKind.DRINK)
    sort_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["store_id", "sort_order", "id"]

    def __str__(self) -> str:
        return self.name


class StockMode(models.TextChoices):
    NONE = "none", "在庫管理しない"
    BOTTLE = "bottle", "本数管理"
    SHOT = "shot", "ショット販売（容量から理論消費）"


class Product(TimestampedModel):
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="products")
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name="products")
    name = models.CharField("商品名", max_length=100)
    price = models.DecimalField("販売価格", max_digits=12, decimal_places=2)
    tax_rate = models.DecimalField("税率(%)", max_digits=5, decimal_places=2, default=Decimal("10.00"))
    is_active = models.BooleanField("販売中", default=True)
    sort_order = models.PositiveIntegerField(default=0)
    # 在庫
    stock_mode = models.CharField(max_length=10, choices=StockMode.choices, default=StockMode.NONE)
    volume_ml = models.DecimalField("1本の容量(ml)", max_digits=8, decimal_places=1, null=True, blank=True)
    shot_ml = models.DecimalField("1杯量(ml)", max_digits=6, decimal_places=1, null=True, blank=True)
    reorder_point = models.DecimalField("発注点（本）", max_digits=10, decimal_places=2, null=True, blank=True)
    min_stock = models.DecimalField("最低在庫（本）", max_digits=10, decimal_places=2, null=True, blank=True)
    cost_price = models.DecimalField("原価（1本あたり・未設定可）", max_digits=12, decimal_places=2, null=True, blank=True)
    is_bottle_keepable = models.BooleanField("ボトルキープ対象", default=False)
    note = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["store_id", "category__sort_order", "sort_order", "id"]
        indexes = [models.Index(fields=["store", "is_active"])]

    def __str__(self) -> str:
        return self.name

    def shots_per_bottle(self) -> Decimal | None:
        if self.stock_mode == StockMode.SHOT and self.volume_ml and self.shot_ml:
            return (self.volume_ml / self.shot_ml)
        return None


class Table(TimestampedModel):
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="tables")
    name = models.CharField("卓名", max_length=30)
    capacity = models.PositiveSmallIntegerField("定員", default=4)
    sort_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["store_id", "sort_order", "id"]
        constraints = [models.UniqueConstraint(fields=["store", "name"], name="uniq_table_name_per_store")]

    def __str__(self) -> str:
        return self.name
