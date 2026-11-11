"""在庫台帳の操作。"""
from __future__ import annotations

from decimal import Decimal

from django.db.models import Sum

from apps.core.money import D, ZERO

from .models import StockMovement, StockMovementKind


def bottles_for_sale(product, quantity: Decimal) -> tuple[Decimal, bool]:
    """販売数量を本数換算する。戻り値: (本数, 理論値か)"""
    quantity = D(quantity)
    if product.stock_mode == "bottle":
        return quantity, False
    if product.stock_mode == "shot" and product.volume_ml and product.shot_ml:
        return (quantity * D(product.shot_ml) / D(product.volume_ml)).quantize(Decimal("0.0001")), True
    return ZERO, False


def record_sale(product, order_line, quantity: Decimal, when, user):
    if product.stock_mode == "none":
        return None
    bottles, theoretical = bottles_for_sale(product, quantity)
    if bottles == 0:
        return None
    return StockMovement.objects.create(
        store=product.store, product=product, kind=StockMovementKind.SALE, quantity=-bottles, is_theoretical=theoretical,
        order_line=order_line, reference=f"伝票#{order_line.visit.slip_number}", occurred_at=when, performed_by=user,
    )


def reverse_sale(order_line, when, user, reason: str = ""):
    rows = StockMovement.objects.filter(order_line=order_line, kind=StockMovementKind.SALE)
    created = []
    for m in rows:
        created.append(StockMovement.objects.create(
            store=m.store, product=m.product, kind=StockMovementKind.SALE_REVERSAL, quantity=-m.quantity,
            is_theoretical=m.is_theoretical, order_line=order_line, reference=m.reference, reason=reason,
            occurred_at=when, performed_by=user,
        ))
    return created


def current_stock(product) -> Decimal:
    agg = StockMovement.objects.filter(product=product).aggregate(s=Sum("quantity"))
    return (agg["s"] or ZERO).quantize(Decimal("0.0001"))


def stock_levels(store) -> dict[int, Decimal]:
    rows = StockMovement.objects.filter(store=store).values("product_id").annotate(s=Sum("quantity"))
    return {r["product_id"]: (r["s"] or ZERO) for r in rows}
