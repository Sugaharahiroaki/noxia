"""
レジ出金と領収書の突合（店舗×営業日）。
POS の現金経費の出金（cash.CashMovement kind=expense）と、承認済み領収書（店舗現金）を比べ、
「領収書なしの出金」「出金に結び付いていない領収書」「金額差」を出す。会計用語は使わない（画面向け）。
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Q

from apps.cash.models import BusinessDay, CashMovement
from apps.core.money import ZERO


def check_day(store, d: date) -> dict:
    from .models import ExpenseReceipt

    day = BusinessDay.objects.filter(store=store, date=d).first()
    moves = list(CashMovement.objects.filter(store=store, kind="expense", is_voided=False, business_day__date=d).select_related("expense_receipt", "account"))
    receipts = list(ExpenseReceipt.objects.filter(store=store, status="approved", approved_payment_method="store_cash").filter(
        Q(approved_date=d) | Q(approved_date__isnull=True, receipt_date=d)).select_related("cash_movement"))
    matched, no_receipt, orphan = [], [], []
    linked_ids = set()
    for mv in moves:
        r = getattr(mv, "expense_receipt", None)
        row = {"cash_movement_id": mv.id, "amount": str(mv.amount), "reason": mv.reason, "account": mv.account.name if mv.account_id else "",
               "occurred_at": mv.occurred_at.isoformat()}
        if r is not None:
            linked_ids.add(r.id)
            row["receipt_id"] = r.id
            matched.append(row)
        else:
            no_receipt.append(row)
    for r in receipts:
        if r.id in linked_ids:
            continue
        orphan.append({"receipt_id": r.id, "amount": str(r.approved_amount or ZERO), "vendor": r.approved_vendor, "cash_movement_id": r.cash_movement_id})
    pos_total = sum((mv.amount for mv in moves), ZERO)
    receipt_total = sum((r.approved_amount or ZERO for r in receipts), ZERO)
    status = "no_data" if day is None else ("match" if pos_total == receipt_total and not no_receipt and not orphan else "diff")
    return {
        "store_id": store.id, "store": store.name, "date": d.isoformat(), "business_day_status": day.status if day else None,
        "pos_expense_total": str(pos_total), "receipt_total": str(receipt_total), "difference": str(pos_total - receipt_total), "status": status,
        "matched": matched, "no_receipt": no_receipt, "receipt_without_movement": orphan,
    }


def check_range(store, start: date, end: date) -> dict:
    rows = []
    d = start
    while d <= end:
        r = check_day(store, d)
        if r["business_day_status"] is not None or r["receipt_total"] != "0":
            rows.append(r)
        d += timedelta(days=1)
    return {"store_id": store.id, "from": start.isoformat(), "to": end.isoformat(), "rows": rows,
            "no_receipt_count": sum(len(r["no_receipt"]) for r in rows), "diff_days": sum(1 for r in rows if r["status"] == "diff")}


def unreceipted_count(store, d: date) -> int:
    """締め画面用: その営業日の「領収書なしの現金経費」件数。"""
    return CashMovement.objects.filter(store=store, kind="expense", is_voided=False, business_day__date=d, expense_receipt__isnull=True).count()


def amount_or_zero(v) -> Decimal:
    return v if isinstance(v, Decimal) else ZERO
