"""
管理用の集計（読み取り専用）。

- ここでは会計・バック・報酬の計算をしない。確定済みの会計（Checkout）・明細・勤怠・バックの結果を集めて並べるだけ。
- DB を引数で受けない部分は純粋な関数（上半分）にして、式をテストで固める。
- 用語: 小計 = Checkout.subtotal（値引き・サービス料の前の明細合計）、本計 = Checkout.total（請求額）。
"""
from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from apps.core.money import D, ZERO, round_yen

YOBI_TYPES = ("nomination", "jounai")   # 「呼び」= 指名（本指名・場内）
DOUHAN_TYPES = ("douhan",)
SET_TYPES = ("course_set", "extension")
SPLIT_MODES = ("equal", "full_each")
COUNT_MODES = ("per_slip", "per_set")


# ---------------------------------------------------------------------------
# 純粋な関数
# ---------------------------------------------------------------------------
def split_amount(amount: Decimal, n: int, mode: str = "equal") -> list[Decimal]:
    """
    伝票の金額を n 人に分ける。
    equal: 均等割り（円未満切り捨て。余りは先頭の人に足して、合計が元の金額と一致するようにする）
    full_each: 全員に全額（合計は元の金額の n 倍になる）
    """
    amount = D(amount)
    if n <= 0:
        return []
    if mode == "full_each":
        return [amount] * n
    each = round_yen(amount / Decimal(n), "floor")
    parts = [each] * n
    parts[0] += amount - each * n
    return parts


def cast_sales_for_slip(subtotal: Decimal, total: Decimal, designations: list[tuple[int, str]], set_count: int,
                        split_mode: str = "equal", count_mode: str = "per_slip") -> dict[int, dict]:
    """
    1 つの伝票のキャスト売上。designations は [(employee_id, "nomination" | "jounai" | "douhan"), ...]（確定済みの会計に入った明細ぶん）。
    指名（本指名・場内）がある伝票の小計・本計を「呼び小計・呼び本計」として、同伴がある伝票の小計・本計を「同伴小計・同伴本計」として、
    その従業員に計上する。対象が複数人なら split_mode で分ける。回数は count_mode（1 伝票 1 回／セット数ぶん）。
    戻り値: {employee_id: {yobi_subtotal, yobi_total, douhan_subtotal, douhan_total, nomination_count, jounai_count, douhan_count}}
    """
    out: dict[int, dict] = {}

    def row(eid: int) -> dict:
        return out.setdefault(eid, {
            "yobi_subtotal": ZERO, "yobi_total": ZERO, "douhan_subtotal": ZERO, "douhan_total": ZERO,
            "nomination_count": Decimal("0"), "jounai_count": Decimal("0"), "douhan_count": Decimal("0"),
        })

    times = Decimal(max(set_count, 1) if count_mode == "per_set" else 1)
    for kinds, sub_key, tot_key in ((YOBI_TYPES, "yobi_subtotal", "yobi_total"), (DOUHAN_TYPES, "douhan_subtotal", "douhan_total")):
        ids = sorted({eid for eid, kind in designations if kind in kinds})
        if not ids:
            continue
        for eid, s, t in zip(ids, split_amount(subtotal, len(ids), split_mode), split_amount(total, len(ids), split_mode)):
            r = row(eid)
            r[sub_key] += s
            r[tot_key] += t
    for kind, key in (("nomination", "nomination_count"), ("jounai", "jounai_count"), ("douhan", "douhan_count")):
        for eid in sorted({e for e, k in designations if k == kind}):
            row(eid)[key] += times
    return out


def ratio_percent(numerator: Decimal, denominator: Decimal, digits: int = 2) -> Decimal | None:
    """割合（%）。分母が 0 なら None。例: 28,075 / 97,800 → 28.71"""
    if not denominator:
        return None
    q = Decimal(1).scaleb(-digits)
    return (D(numerator) * 100 / D(denominator)).quantize(q, rounding=ROUND_HALF_UP)


def per_unit(amount: Decimal, count: int) -> Decimal | None:
    """単価（四捨五入）。例: 488,300 / 41 → 11,910。count が 0 なら None。"""
    if not count:
        return None
    return round_yen(D(amount) / Decimal(count), "half_up")


# ---------------------------------------------------------------------------
# DB から読む部分
# ---------------------------------------------------------------------------
def report_settings(store) -> dict:
    st = getattr(store, "settings", None)
    split = getattr(st, "cast_sales_split_mode", "equal") or "equal"
    count = getattr(st, "nomination_count_mode", "per_slip") or "per_slip"
    return {
        "cast_sales_split_mode": split if split in SPLIT_MODES else "equal",
        "nomination_count_mode": count if count in COUNT_MODES else "per_slip",
        "withholding_enabled": bool(getattr(st, "withholding_enabled", False)),
    }


def visits_for_reports(store, f: date, t: date):
    """集計に使う伝票（取消済みも含む。使う側で分ける）。明細・会計・支払い・タグ・客の行をまとめて読む。"""
    from django.db.models import Prefetch

    from apps.sales.models import OrderLine, Visit

    return Visit.objects.filter(store=store, business_date__gte=f, business_date__lte=t).select_related("table", "customer").prefetch_related(
        Prefetch("lines", queryset=OrderLine.objects.select_related("checkout").prefetch_related("back_targets__employee")),
        "checkouts__payments", "checkouts__refunds", "visit_tags__tag", "course_entries", "guests",
    ).order_by("business_date", "slip_number")


def slip_summary(visit, settings: dict) -> dict:
    """
    伝票 1 件の集計値。金額は確定済みの会計（取消を除く）の合計。営業中の伝票は会計が無いので 0。
    """
    confirmed = [c for c in visit.checkouts.all() if c.status == "confirmed"]
    confirmed_ids = {c.id for c in confirmed}
    pay = {"cash": ZERO, "card": ZERO, "receivable": ZERO}
    tendered = ZERO
    change = ZERO
    refunds = ZERO
    for c in confirmed:
        for p in c.payments.all():
            if p.status == "valid":
                pay[p.method] += p.amount
                if p.method == "cash":
                    tendered += p.tendered or ZERO
                    change += p.change_amount or ZERO
        refunds += sum((r.amount for r in c.refunds.all()), ZERO)
    subtotal = sum((c.subtotal for c in confirmed), ZERO)
    total = sum((c.total for c in confirmed), ZERO)
    paid_lines = [l for l in visit.lines.all() if l.status == "active" and l.checkout_id in confirmed_ids]
    designations: list[tuple[int, str]] = []
    names: dict[int, str] = {}
    for l in paid_lines:
        if l.line_type in YOBI_TYPES + DOUHAN_TYPES:
            for bt in l.back_targets.all():
                designations.append((bt.employee_id, l.line_type))
                names[bt.employee_id] = bt.employee.display_name
    set_count = sum(1 for l in paid_lines if l.line_type in SET_TYPES)
    cast = cast_sales_for_slip(subtotal, total, designations, set_count, settings["cast_sales_split_mode"], settings["nomination_count_mode"])
    closed_at = visit.closed_at
    minutes = int((closed_at - visit.opened_at).total_seconds() // 60) if closed_at else None
    return {
        "visit": visit,
        "confirmed": confirmed,
        "subtotal": subtotal,
        "discount": sum((c.discount_total for c in confirmed), ZERO),
        "service_charge": sum((c.service_charge for c in confirmed), ZERO),
        "tax": sum((c.tax_total for c in confirmed), ZERO),
        "total": total,
        "cash": pay["cash"], "card": pay["card"], "receivable": pay["receivable"],
        "tendered": tendered, "change": change, "refunds": refunds,
        "voided_checkouts": sum(1 for c in visit.checkouts.all() if c.status != "confirmed"),
        "minutes": minutes,
        "set_count": set_count,
        "cast": cast,
        "cast_names": names,
        "names_by_kind": {k: sorted({names[e] for e, kind in designations if kind == k}) for k in ("nomination", "jounai", "douhan")},
        "paid_lines": paid_lines,
    }


def slip_row(s: dict) -> dict:
    """伝票一覧の 1 行（JSON 用）。"""
    v = s["visit"]
    return {
        "id": v.id, "slip_number": v.slip_number, "business_date": v.business_date.isoformat(), "status": v.status,
        "status_label": v.get_status_display(), "table_name": v.table.name,
        "opened_at": v.opened_at.isoformat(), "closed_at": v.closed_at.isoformat() if v.closed_at else None, "minutes": s["minutes"],
        "guest_count": v.guest_count,
        "customer": (v.customer.display_name if v.customer_id and v.customer else v.customer_name) or "",
        "tags": [{"name": vt.tag.name, "color": vt.tag.color} for vt in v.visit_tags.all()],
        "nomination": s["names_by_kind"]["nomination"], "jounai": s["names_by_kind"]["jounai"], "douhan": s["names_by_kind"]["douhan"],
        "subtotal": str(s["subtotal"]), "service_charge": str(s["service_charge"]), "discount": str(s["discount"]),
        "cash": str(s["cash"]), "card": str(s["card"]), "receivable": str(s["receivable"]), "total": str(s["total"]),
        "refunds": str(s["refunds"]), "checkout_count": len(s["confirmed"]), "voided_checkouts": s["voided_checkouts"],
        "cancel_reason": v.cancel_reason,
    }


SLIP_TOTAL_KEYS = ("subtotal", "service_charge", "discount", "cash", "card", "receivable", "total", "refunds")


def slip_list(store, f: date, t: date, *, payment: str = "", discount_only: bool = False, cancelled: bool = False) -> dict:
    """
    伝票一覧。既定は取消済みを除く。cancelled=True で取消済みの伝票だけを出す。
    payment: cash / card / receivable（その支払方法を含む伝票）。discount_only: 値引きがある伝票。
    """
    settings = report_settings(store)
    rows = []
    totals = {k: ZERO for k in SLIP_TOTAL_KEYS}
    guests = 0
    for v in visits_for_reports(store, f, t):
        if cancelled != (v.status == "cancelled"):
            continue
        s = slip_summary(v, settings)
        if payment in ("cash", "card", "receivable") and s[payment] <= 0:
            continue
        if discount_only and s["discount"] <= 0:
            continue
        rows.append(slip_row(s))
        for k in SLIP_TOTAL_KEYS:
            totals[k] += s[k]
        guests += v.guest_count
    return {
        "from": f.isoformat(), "to": t.isoformat(), "rows": rows,
        "totals": {**{k: str(v) for k, v in totals.items()}, "count": len(rows), "guest_count": guests},
        "settings": settings,
    }


def slip_detail(store, visit_id) -> dict | None:
    v = visits_for_reports(store, date.min, date.max).filter(id=visit_id).first()
    if v is None:
        return None
    settings = report_settings(store)
    s = slip_summary(v, settings)
    lines = []
    for l in v.lines.all():
        lines.append({
            "id": l.id, "line_type": l.line_type, "line_type_label": l.get_line_type_display(), "name": l.name,
            "cast": [bt.employee.display_name for bt in l.back_targets.all()],
            "unit_price": str(l.unit_price), "tax_rate": str(l.tax_rate), "tax_included": l.tax_included, "quantity": str(l.quantity),
            "amount": str(l.amount), "status": l.status, "cancel_reason": l.cancel_reason,
            "checkout_number": l.checkout.number if l.checkout_id else None,
            "paid": l.status == "active" and l.checkout_id is not None and l.checkout.status == "confirmed",
        })
    cast_rows = [
        {"employee_id": eid, "employee_name": s["cast_names"].get(eid, ""), **{k: str(val) for k, val in vals.items()}}
        for eid, vals in sorted(s["cast"].items(), key=lambda kv: s["cast_names"].get(kv[0], ""))
    ]
    checkouts = [{
        "id": c.id, "number": c.number, "kind": c.kind, "status": c.status, "status_label": c.get_status_display(),
        "confirmed_at": c.confirmed_at.isoformat(), "subtotal": str(c.subtotal), "discount_total": str(c.discount_total),
        "service_charge": str(c.service_charge), "tax_total": str(c.tax_total), "rounding_adjustment": str(c.rounding_adjustment), "total": str(c.total),
        "payments": [{"method": p.method, "method_label": p.get_method_display(), "amount": str(p.amount), "tendered": str(p.tendered) if p.tendered is not None else None,
                      "change_amount": str(p.change_amount), "status": p.status} for p in c.payments.all()],
        "refunds": [{"amount": str(r.amount), "method": r.method, "reason": r.reason} for r in c.refunds.all()],
    } for c in v.checkouts.all()]
    return {
        **slip_row(s),
        "note": v.note, "present_count": sum(1 for g in v.guests.all() if g.status == "present") if v.guests.all() else v.guest_count,
        "courses": [e.course_name for e in v.course_entries.all()],
        "tax": str(s["tax"]), "tendered": str(s["tendered"]), "change": str(s["change"]), "set_count": s["set_count"],
        "lines": lines, "checkouts": checkouts, "cast_sales": cast_rows, "settings": settings,
    }
