from __future__ import annotations

from rest_framework import serializers

from apps.catalog.models import Product
from apps.core.api import StorePK

from .models import GoodsReceipt, GoodsReceiptLine, PurchaseOrder, PurchaseOrderLine, StockMovement, StockTake, StockTakeLine, Supplier, SupplierProduct


class SupplierSerializer(serializers.ModelSerializer):
    class Meta:
        model = Supplier
        fields = ["id", "name", "contact", "order_method", "is_active", "note"]
        read_only_fields = ["id"]


class SupplierProductSerializer(serializers.ModelSerializer):
    supplier = StorePK(model=Supplier)
    product = StorePK(model=Product)
    supplier_name = serializers.CharField(source="supplier.name", read_only=True)
    product_name = serializers.CharField(source="product.name", read_only=True)

    class Meta:
        model = SupplierProduct
        fields = ["id", "supplier", "supplier_name", "product", "product_name", "purchase_price", "order_unit_name", "units_per_order_unit",
                  "lead_days", "is_preferred"]
        read_only_fields = ["id"]


class StockMovementSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source="product.name", read_only=True)
    kind_label = serializers.CharField(source="get_kind_display", read_only=True)
    performed_by_name = serializers.SerializerMethodField()

    class Meta:
        model = StockMovement
        fields = ["id", "product", "product_name", "kind", "kind_label", "quantity", "is_theoretical", "order_line", "reference", "reason",
                  "occurred_at", "performed_by_name"]

    def get_performed_by_name(self, obj):
        return str(obj.performed_by) if obj.performed_by else ""


class StockTakeLineSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source="product.name", read_only=True)

    class Meta:
        model = StockTakeLine
        fields = ["id", "product", "product_name", "theoretical_qty", "counted_full_bottles", "counted_open_ml", "counted_qty", "difference", "note"]


class StockTakeSerializer(serializers.ModelSerializer):
    lines = StockTakeLineSerializer(many=True, read_only=True)
    performed_by_name = serializers.SerializerMethodField()

    class Meta:
        model = StockTake
        fields = ["id", "taken_at", "status", "applied_at", "performed_by_name", "note", "lines", "created_at"]

    def get_performed_by_name(self, obj):
        return str(obj.performed_by) if obj.performed_by else ""


class PurchaseOrderLineSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source="product.name", read_only=True)
    expected_bottles = serializers.SerializerMethodField()
    order_unit_name = serializers.CharField(source="supplier_product.order_unit_name", read_only=True, default="本")

    class Meta:
        model = PurchaseOrderLine
        fields = ["id", "product", "product_name", "supplier_product", "order_units", "units_per_order_unit", "unit_price", "expected_bottles",
                  "received_bottles", "order_unit_name", "note"]

    def get_expected_bottles(self, obj):
        from decimal import Decimal

        return str(obj.expected_bottles.quantize(Decimal("0.01")))


class GoodsReceiptSerializer(serializers.ModelSerializer):
    lines = serializers.SerializerMethodField()

    class Meta:
        model = GoodsReceipt
        fields = ["id", "received_at", "note", "lines"]

    def get_lines(self, obj):
        return [{"po_line": l.po_line_id, "bottles": str(l.bottles)} for l in obj.lines.all()]


class PurchaseOrderSerializer(serializers.ModelSerializer):
    lines = PurchaseOrderLineSerializer(many=True, read_only=True)
    receipts = GoodsReceiptSerializer(many=True, read_only=True)
    supplier_name = serializers.CharField(source="supplier.name", read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    total_cost = serializers.SerializerMethodField()

    class Meta:
        model = PurchaseOrder
        fields = ["id", "number", "supplier", "supplier_name", "status", "status_label", "confirmed_at", "ordered_at", "ordered_via", "expected_on",
                  "note", "total_cost", "lines", "receipts", "created_at"]

    def get_total_cost(self, obj):
        return str(sum((l.order_units * l.unit_price for l in obj.lines.all()), 0))
