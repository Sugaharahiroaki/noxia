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
        "id": v.id, "public_id": v.public_id, "slip_number": v.slip_number, "business_date": v.business_date.isoformat(), "status": v.status,
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
        "id": c.id, "public_id": c.public_id, "number": c.number, "kind": c.kind, "status": c.status, "status_label": c.get_status_display(),
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


# ---------------------------------------------------------------------------
# M2: 日次・月次収支
# ---------------------------------------------------------------------------
# 入出金（CashMovement.kind）を粗利に含めるか。M6 で入出金項目のマスタ（CashItem）を作るまでは kind で決める。
#   入金として粗利に足す: other_in（その他入金）
#   出金として粗利から引く: expense（現金経費）・other_out（その他出金）
#   含めない: daily_pay（日払い。人件費として別に引くため）・mid_collection（途中回収。現金の移動）・float_adjust_in（釣銭補充。現金の移動）
PROFIT_IN_KINDS = ("other_in",)
PROFIT_OUT_KINDS = ("expense", "other_out")
PROFIT_EXCLUDED_KINDS = ("daily_pay", "mid_collection", "float_adjust_in")
PROFIT_KIND_NOTE = "粗利に含める入出金: 入金は「その他入金」、出金は「現金経費」「その他出金」。日払い（人件費として別に計上）・途中回収・釣銭補充は含めません。"


def profit_metrics(*, sales: Decimal, income: Decimal = ZERO, labor_cast: Decimal = ZERO, labor_staff: Decimal = ZERO,
                   expenses: Decimal = ZERO, guests: int = 0, groups: int = 0) -> dict:
    """
    収支の式（参考設計 6.6）。
      経費計 = 人件費（キャスト＋スタッフ）＋ 出金（粗利対象）
      粗利   = 売上計 ＋ 入金（粗利対象）− 経費計          例: 97,800 − 28,075 = 69,725
      給率   = キャスト人件費 ÷ 売上計                      例: 28,075 / 97,800 = 28.71%
      客単価 = 売上 ÷ 来店人数（四捨五入）                  例: 488,300 / 41 = 11,910
      組単価 = 売上 ÷ 来店組数（四捨五入）                  例: 488,300 / 20 = 24,415
    """
    labor = D(labor_cast) + D(labor_staff)
    cost = labor + D(expenses)
    return {
        "labor_total": labor,
        "cost_total": cost,
        "gross_profit": D(sales) + D(income) - cost,
        "pay_ratio": ratio_percent(labor_cast, sales),
        "per_guest": per_unit(sales, guests),
        "per_group": per_unit(sales, groups),
    }


DAY_SUM_KEYS = ("sales", "cash", "card", "receivable", "cast_sales", "yobi_subtotal", "douhan_subtotal", "labor_cast", "labor_staff",
                "income", "expenses", "daily_pay_cast", "daily_pay_staff", "bonus", "minus", "refunds")
DAY_COUNT_KEYS = ("guests", "groups", "yobi_groups", "checkouts")


def _empty_day(d: date) -> dict:
    row = {"date": d, **{k: ZERO for k in DAY_SUM_KEYS}, **{k: 0 for k in DAY_COUNT_KEYS}, "nomination_count": Decimal("0")}
    return row


def store_roles(store) -> dict[int, str]:
    """従業員 ID → この店舗での役割（cast / staff / manager）。配属が無い人は staff として扱う。"""
    from apps.staff.models import EmployeeStoreAssignment

    return dict(EmployeeStoreAssignment.objects.filter(store=store).values_list("employee_id", "role"))


def daily_metrics(store, f: date, t: date) -> list[dict]:
    """
    営業日ごとの収支の元の数字（f〜t の全日。伝票が無い日は 0）。
    売上は確定済みの会計、人件費は「時給×勤務時間（既存の shift_wage）＋その日の確定バック（既存の BackEntry）」。新しい計算はしない。
    """
    from datetime import timedelta

    from django.db.models import Count, Sum

    from apps.cash.models import CashMovement
    from apps.sales.models import Checkout, Payment, Refund
    from apps.staff.models import BackEntry, EmployeePayment, Shift
    from apps.staff.payroll import shift_minutes, shift_wage

    settings = report_settings(store)
    days: dict[date, dict] = {}
    d = f
    while d <= t:
        days[d] = _empty_day(d)
        d += timedelta(days=1)
    rng = dict(business_date__gte=f, business_date__lte=t)

    for r in Checkout.objects.filter(store=store, status="confirmed", **rng).values("business_date").annotate(s=Sum("total"), n=Count("id")):
        days[r["business_date"]]["sales"] = r["s"] or ZERO
        days[r["business_date"]]["checkouts"] = r["n"]
    for r in Payment.objects.filter(store=store, status="valid", checkout__status="confirmed", **rng).values("business_date", "method").annotate(s=Sum("amount")):
        if r["method"] in ("cash", "card", "receivable"):
            days[r["business_date"]][r["method"]] = r["s"] or ZERO
    for r in Refund.objects.filter(store=store, **rng).values("business_date").annotate(s=Sum("amount")):
        days[r["business_date"]]["refunds"] = r["s"] or ZERO

    # 伝票（来店人数・組数・キャスト売上）。伝票の営業日 = 入店した営業日
    for v in visits_for_reports(store, f, t):
        if v.status == "cancelled":
            continue
        row = days[v.business_date]
        row["guests"] += v.guest_count
        row["groups"] += 1
        s = slip_summary(v, settings)
        kinds = {k for _, k in _designations(s)}
        if kinds & set(YOBI_TYPES):
            row["cast_sales"] += s["total"]     # 女子売上 = 呼び本計（指名のある伝票の本計。伝票ごとに 1 回）
            row["yobi_subtotal"] += s["subtotal"]
            row["yobi_groups"] += 1
        if kinds & set(DOUHAN_TYPES):
            row["douhan_subtotal"] += s["subtotal"]
        for vals in s["cast"].values():
            row["nomination_count"] += vals["nomination_count"] + vals["jounai_count"]

    # 人件費（役割ごと）
    roles = store_roles(store)
    is_cast = lambda eid: roles.get(eid) == "cast"  # noqa: E731
    for sh in Shift.objects.filter(store=store, **rng).select_related("employee", "store").prefetch_related("breaks"):
        _, amount = shift_wage(sh, shift_minutes(sh))
        days[sh.business_date]["labor_cast" if is_cast(sh.employee_id) else "labor_staff"] += amount
    for r in BackEntry.objects.filter(store=store, status="confirmed", **rng).values("business_date", "employee_id").annotate(s=Sum("amount")):
        days[r["business_date"]]["labor_cast" if is_cast(r["employee_id"]) else "labor_staff"] += r["s"] or ZERO
    # 手入力の調整（既存の「バック調整」）は 1 件ずつ見て、正はボーナス、負はマイナスとして見せる（人件費には上で含めてある）
    for bd, amount in BackEntry.objects.filter(store=store, status="confirmed", kind="adjustment", **rng).values_list("business_date", "amount"):
        if amount >= 0:
            days[bd]["bonus"] += amount
        else:
            days[bd]["minus"] += -amount
    for r in EmployeePayment.objects.filter(store=store, is_voided=False, kind="daily", **rng).values("business_date", "employee_id").annotate(s=Sum("amount")):
        days[r["business_date"]]["daily_pay_cast" if is_cast(r["employee_id"]) else "daily_pay_staff"] += r["s"] or ZERO

    # 入出金（粗利に含めるもの）
    for r in CashMovement.objects.filter(store=store, is_voided=False, business_day__date__gte=f, business_day__date__lte=t).values("business_day__date", "kind").annotate(s=Sum("amount")):
        row = days.get(r["business_day__date"])
        if row is None:
            continue
        if r["kind"] in PROFIT_IN_KINDS:
            row["income"] += r["s"] or ZERO
        elif r["kind"] in PROFIT_OUT_KINDS:
            row["expenses"] += r["s"] or ZERO
    return [days[k] for k in sorted(days)]


def _designations(s: dict) -> list[tuple[int, str]]:
    out = []
    for l in s["paid_lines"]:
        if l.line_type in YOBI_TYPES + DOUHAN_TYPES:
            out.extend((bt.employee_id, l.line_type) for bt in l.back_targets.all())
    return out


def sum_days(rows: list[dict]) -> dict:
    total = _empty_day(rows[0]["date"] if rows else date.today())
    for r in rows:
        for k in DAY_SUM_KEYS:
            total[k] += r[k]
        for k in DAY_COUNT_KEYS:
            total[k] += r[k]
        total["nomination_count"] += r["nomination_count"]
    return total


def day_json(r: dict) -> dict:
    """1 日ぶん（または合計）の表示用。式は profit_metrics。"""
    m = profit_metrics(sales=r["sales"], income=r["income"], labor_cast=r["labor_cast"], labor_staff=r["labor_staff"],
                       expenses=r["expenses"], guests=r["guests"], groups=r["groups"])
    out = {k: str(r[k]) for k in DAY_SUM_KEYS}
    out.update({k: r[k] for k in DAY_COUNT_KEYS})
    out.update({
        "date": r["date"].isoformat(), "nomination_count": str(r["nomination_count"]),
        "labor_total": str(m["labor_total"]), "cost_total": str(m["cost_total"]), "gross_profit": str(m["gross_profit"]),
        "pay_ratio": str(m["pay_ratio"]) if m["pay_ratio"] is not None else None,
        "per_guest": str(m["per_guest"]) if m["per_guest"] is not None else None,
        "per_group": str(m["per_group"]) if m["per_group"] is not None else None,
        "has_slips": r["groups"] > 0 or r["sales"] != 0,
    })
    return out


def month_range(year: int, month: int) -> tuple[date, date]:
    from datetime import timedelta

    first = date(year, month, 1)
    nxt = date(year + (month // 12), (month % 12) + 1, 1)
    return first, nxt - timedelta(days=1)


def monthly_report(store, year: int, month: int) -> dict:
    f, t = month_range(year, month)
    rows = daily_metrics(store, f, t)
    total = sum_days(rows)
    active_days = sum(1 for r in rows if r["groups"] > 0 or r["sales"] != 0)
    cumulative = ZERO
    out_rows = []
    for r in rows:
        j = day_json(r)
        cumulative += D(j["gross_profit"])
        j["gross_profit_cumulative"] = str(cumulative)
        out_rows.append(j)
    tj = day_json(total)
    return {
        "month": f"{year}-{month:02d}", "from": f.isoformat(), "to": t.isoformat(), "rows": out_rows, "totals": tj, "active_days": active_days,
        "average": {k: (str(round_yen(D(tj[k]) / active_days, "half_up")) if active_days else None) for k in ("sales", "cast_sales", "labor_cast", "gross_profit")},
        "profit_kind_note": PROFIT_KIND_NOTE,
        "settings": report_settings(store),
    }


def cash_days(store, f: date, t: date) -> list[dict]:
    """レジ精算の一覧（営業日ごと）。数字は既存の cash.day_summary と、締めのときに保存した実際の現金・差額。式は変えない。"""
    from apps.cash.models import BusinessDay
    from apps.cash.services import day_summary

    rows = []
    for day in BusinessDay.objects.filter(store=store, date__gte=f, date__lte=t).order_by("date"):
        s = day_summary(day)
        cash_in = D(s["cash_in_other"]) + D(s["receivable_collected_cash"])
        cash_out = D(s["cash_out_expense"]) + D(s["cash_out_daily_pay"]) + D(s["cash_out_other"]) + D(s["cash_out_mid_collection"]) + D(s["cash_refunds"])
        rows.append({
            "date": day.date.isoformat(), "status": day.status, "status_label": day.get_status_display(),
            "opening_float": s["opening_float"], "cash_sales": s["cash_sales"], "cash_in": str(cash_in), "cash_out": str(cash_out),
            "daily_pay": s["cash_out_daily_pay"], "theoretical_cash": s["theoretical_cash"],
            "counted_cash": str(day.counted_cash) if day.counted_cash is not None else None,
            "difference": str(day.difference) if day.difference is not None else None,
            "set_aside_amount": str(day.set_aside_amount) if day.set_aside_amount is not None else None,
            "next_day_float": str(day.next_day_float) if day.next_day_float is not None else None,
            "difference_reason": day.difference_reason,
        })
    return rows


CASH_FORMULA_NOTE = (
    "理論現金は NOXIA の既存の式です: 開始時釣銭 ＋ その日に受け取った現金 ＋ 未収回収（現金）＋ その他入金・釣銭補充 − 現金返金 − 現金経費 − 日払い − その他出金 − 途中回収。"
    "「入金」には未収の回収（現金）を、「出金」には現金の返金を含めて表示しています。"
)


def daily_report(store, d: date) -> dict:
    """営業日報（1 日）。当日・当月累計・1 営業日あたりの平均、現金管理、入出金、伝票の一覧。"""
    from apps.cash.models import BusinessDay, CashMovement

    f, t = month_range(d.year, d.month)
    month_rows = daily_metrics(store, f, min(t, d) if d >= f else t)
    upto = [r for r in month_rows if r["date"] <= d]
    today = next((r for r in month_rows if r["date"] == d), _empty_day(d))
    cum = sum_days(upto)
    active_days = sum(1 for r in upto if r["groups"] > 0 or r["sales"] != 0)
    cj = day_json(cum)
    avg_keys = ("sales", "cash", "card", "receivable", "cast_sales", "labor_cast", "gross_profit")
    cash = cash_days(store, f, d)
    day_cash = next((c for c in cash if c["date"] == d.isoformat()), None)
    bd = BusinessDay.objects.filter(store=store, date=d).first()
    moves = []
    if bd is not None:
        for m in CashMovement.objects.filter(business_day=bd).select_related("employee").order_by("occurred_at"):
            moves.append({
                "id": m.id, "kind": m.kind, "kind_label": m.get_kind_display(), "direction": "in" if m.is_inflow else "out", "amount": str(m.amount),
                "reason": m.reason, "employee_name": m.employee.display_name if m.employee_id else "", "occurred_at": m.occurred_at.isoformat(),
                "is_voided": m.is_voided, "in_profit": m.kind in PROFIT_IN_KINDS + PROFIT_OUT_KINDS,
            })
    slips = slip_list(store, d, d)
    return {
        "date": d.isoformat(), "today": day_json(today), "cumulative": cj, "active_days": active_days,
        "average": {k: (str(round_yen(D(cj[k]) / active_days, "half_up")) if active_days else None) for k in avg_keys},
        "cash": day_cash,
        "cash_month": {
            "difference": str(sum((D(c["difference"]) for c in cash if c["difference"] is not None), ZERO)),
            "set_aside": str(sum((D(c["set_aside_amount"]) for c in cash if c["set_aside_amount"] is not None), ZERO)),
        },
        "movements": moves, "slips": slips["rows"], "slip_totals": slips["totals"],
        "profit_kind_note": PROFIT_KIND_NOTE, "cash_formula_note": CASH_FORMULA_NOTE, "settings": report_settings(store),
    }