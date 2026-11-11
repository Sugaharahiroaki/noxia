"""
期首・期末棚卸の評価（最終仕入原価法を既定）と決算整理仕訳。

- 既存の棚卸し（inventory.StockTake）の数量 × 単価で下書きを作る。単価は 最新の仕入先単価（SupplierProduct.purchase_price ÷ 入数）→ 商品の原価（Product.cost_price）→ 0（要確認）。
- 期末: 借方 商品（棚卸資産）／貸方 期末商品棚卸高。期首: 借方 期首商品棚卸高／貸方 商品。翌年の期首は前年の期末から作る。
"""
from __future__ import annotations

from decimal import ROUND_FLOOR, Decimal

from django.db import transaction
from django.utils import timezone

from apps.accounting import books
from apps.accounting.models import FiscalYear, TaxPayer
from apps.core.exceptions import ApiError
from apps.core.money import D, ZERO

from .models import InventoryClosing, InventoryClosingLine, InventoryKind


def _floor(v) -> Decimal:
    return D(v).quantize(Decimal("1"), rounding=ROUND_FLOOR)


def unit_cost_for(product) -> tuple[Decimal, str]:
    """商品 1 本の原価と出所。"""
    from apps.inventory.models import SupplierProduct

    sp = SupplierProduct.objects.filter(product=product).order_by("-is_preferred", "-updated_at").first()
    if sp is not None and sp.units_per_order_unit and sp.units_per_order_unit > 0:
        return (D(sp.purchase_price) / D(sp.units_per_order_unit)).quantize(Decimal("0.01")), "supplier_product"
    if product.cost_price is not None:
        return D(product.cost_price), "product_cost"
    return ZERO, "none"


def lines_from_stock_take(stock_take) -> list[dict]:
    rows = []
    for ln in stock_take.lines.select_related("product"):
        qty = D(ln.counted_qty)
        if qty <= 0:
            continue
        cost, src = unit_cost_for(ln.product)
        rows.append({"product_id": ln.product_id, "name": ln.product.name, "quantity": qty, "unit_cost": cost, "amount": _floor(qty * cost), "cost_source": src})
    return rows


def _check_year(fy: FiscalYear) -> None:
    if fy.is_locked:
        raise ApiError("year_locked", f"{fy.year}年度は締め済みです。再開してから棚卸を変更してください。", status=409)


@transaction.atomic
def create_draft(taxpayer: TaxPayer, user, *, year: int, store, kind: str, stock_take=None, lines: list[dict] | None = None, as_of=None,
                 method: str = "last_purchase_cost", note: str = "") -> InventoryClosing:
    if kind not in InventoryKind.values:
        raise ApiError("validation_error", "期首か期末を選んでください。")
    fy = books.get_fiscal_year(taxpayer, year, create=True)
    _check_year(fy)
    existing = InventoryClosing.objects.filter(fiscal_year=fy, store=store, kind=kind).first()
    if existing is not None:
        if existing.status == "confirmed":
            raise ApiError("already_confirmed", "この年度・店舗の棚卸は確定済みです。直すには確定を取り消してください。", status=409)
        existing.lines.all().delete()
        closing = existing
        closing.stock_take, closing.method, closing.note = stock_take, method, (note or "")[:300]
    else:
        closing = InventoryClosing(taxpayer=taxpayer, fiscal_year=fy, store=store, kind=kind, stock_take=stock_take, method=method, created_by=user, note=(note or "")[:300])
    closing.as_of = as_of or (fy.start_date if kind == InventoryKind.OPENING else fy.end_date)
    if stock_take is not None:
        if stock_take.store_id != store.id:
            raise ApiError("validation_error", "別の店舗の棚卸し結果です。")
        rows = lines_from_stock_take(stock_take)
    else:
        rows = []
        for r in lines or []:
            qty, cost = D(r.get("quantity") or 0), D(r.get("unit_cost") or 0)
            if qty < 0 or cost < 0:
                raise ApiError("validation_error", "数量・単価は 0 以上で入力してください。")
            rows.append({"product_id": r.get("product_id"), "name": (r.get("name") or "")[:120] or "（名称なし）", "quantity": qty, "unit_cost": cost,
                         "amount": _floor(qty * cost), "cost_source": "manual"})
        closing.method = "manual"
    closing.total_amount = sum((r["amount"] for r in rows), ZERO)
    closing.status = "draft"
    closing.save()
    InventoryClosingLine.objects.bulk_create([InventoryClosingLine(closing=closing, product_id=r["product_id"], name=r["name"], quantity=r["quantity"],
                                                                  unit_cost=r["unit_cost"], amount=r["amount"], cost_source=r["cost_source"]) for r in rows])
    return closing


@transaction.atomic
def confirm(closing: InventoryClosing, user) -> InventoryClosing:
    closing = InventoryClosing.objects.select_for_update().get(pk=closing.pk)
    _check_year(closing.fiscal_year)
    if closing.status == "confirmed":
        raise ApiError("already_confirmed", "すでに確定しています。", status=409)
    closing.total_amount = sum((ln.amount for ln in closing.lines.all()), ZERO)
    if closing.total_amount > 0:
        if closing.kind == InventoryKind.CLOSING:
            lines = [{"side": "debit", "key": "inventory", "amount": str(closing.total_amount), "memo": "期末商品棚卸高"},
                     {"side": "credit", "key": "closing_inventory", "amount": str(closing.total_amount), "memo": "期末商品棚卸高"}]
            desc = f"期末棚卸 {closing.fiscal_year.year}年度 {closing.store.name}"
        else:
            lines = [{"side": "debit", "key": "opening_inventory", "amount": str(closing.total_amount), "memo": "期首商品棚卸高"},
                     {"side": "credit", "key": "inventory", "amount": str(closing.total_amount), "memo": "期首商品棚卸高"}]
            desc = f"期首棚卸 {closing.fiscal_year.year}年度 {closing.store.name}"
        closing.journal = books.create_manual_journal(closing.taxpayer, user, issue_date=closing.as_of, description=desc[:200], lines=lines, entry_kind="adjustment",
                                                      store=closing.store, source_type="inventory_closing", source_id=str(closing.id))
    closing.status = "confirmed"
    closing.confirmed_by, closing.confirmed_at = user, timezone.now()
    closing.save()
    return closing


@transaction.atomic
def unconfirm(closing: InventoryClosing, user, *, reason: str) -> InventoryClosing:
    closing = InventoryClosing.objects.select_for_update().get(pk=closing.pk)
    _check_year(closing.fiscal_year)
    if closing.status != "confirmed":
        raise ApiError("not_confirmed", "確定していない棚卸です。", status=409)
    if not (reason or "").strip():
        raise ApiError("validation_error", "取消理由を入力してください。")
    if closing.journal_id and not closing.journal.is_void:
        books.void_manual_journal(closing.journal, user, reason=reason)
    closing.journal = None
    closing.status, closing.confirmed_by, closing.confirmed_at = "draft", None, None
    closing.save()
    return closing


@transaction.atomic
def opening_from_previous(taxpayer: TaxPayer, user, *, year: int, store) -> InventoryClosing:
    """年度 year の期首棚卸を、前年度の確定した期末棚卸から作る。"""
    prev = InventoryClosing.objects.filter(taxpayer=taxpayer, store=store, kind=InventoryKind.CLOSING, fiscal_year__year=year - 1, status="confirmed").first()
    if prev is None:
        raise ApiError("not_found", f"{year - 1}年度の確定した期末棚卸がありません。先に前年度の期末棚卸を確定してください。", status=404)
    rows = [{"product_id": ln.product_id, "name": ln.name, "quantity": str(ln.quantity), "unit_cost": str(ln.unit_cost)} for ln in prev.lines.all()]
    c = create_draft(taxpayer, user, year=year, store=store, kind=InventoryKind.OPENING, lines=rows, note=f"{year - 1}年度の期末棚卸から作成")
    c.method = prev.method
    for ln, src in zip(c.lines.order_by("id"), prev.lines.order_by("id")):
        ln.cost_source = src.cost_source
        ln.save(update_fields=["cost_source"])
    c.save(update_fields=["method", "updated_at"])
    return c


def payload(c: InventoryClosing, *, with_lines: bool = True) -> dict:
    data = {
        "id": c.id, "year": c.fiscal_year.year, "store": c.store_id, "store_name": c.store.name, "kind": c.kind, "kind_label": c.get_kind_display(),
        "as_of": c.as_of, "method": c.method, "method_label": c.get_method_display(), "total_amount": str(c.total_amount), "stock_take": c.stock_take_id,
        "status": c.status, "confirmed_at": c.confirmed_at, "journal_id": c.journal_id, "note": c.note,
        "unpriced_count": c.lines.filter(cost_source="none").count(),
    }
    if with_lines:
        data["lines"] = [{"id": ln.id, "product": ln.product_id, "name": ln.name, "quantity": str(ln.quantity), "unit_cost": str(ln.unit_cost),
                          "amount": str(ln.amount), "cost_source": ln.cost_source} for ln in c.lines.all()]
    return data
