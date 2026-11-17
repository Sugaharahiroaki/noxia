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
# 入出金を粗利に含めるか。
#   入出金項目（CashItem）を選んで登録したもの: 登録した時点の項目の「粗利に含める」（CashMovement.affects_profit）に従う。
#   項目なし（これまでの記録・項目を選ばなかった記録）: 種別（kind）の既定に従う。
#     入金として粗利に足す: other_in（その他入金）
#     出金として粗利から引く: expense（現金経費）・other_out（その他出金）
#     含めない: daily_pay（日払い。人件費として別に引くため）・mid_collection（途中回収。現金の移動）・float_adjust_in（釣銭補充。現金の移動）
PROFIT_IN_KINDS = ("other_in",)
PROFIT_OUT_KINDS = ("expense", "other_out")
PROFIT_EXCLUDED_KINDS = ("daily_pay", "mid_collection", "float_adjust_in")
INFLOW_KINDS = ("other_in", "float_adjust_in")
PROFIT_KIND_NOTE = (
    "粗利に含める入出金: 入出金項目を選んで登録したものは、その項目の「粗利に含める」の設定（登録した時点）に従います。"
    "項目なしのものは、入金は「その他入金」、出金は「現金経費」「その他出金」を含めます。日払い（人件費として別に計上）・途中回収・釣銭補充は含めません。"
)


def movement_profit_side(kind: str, affects_profit: bool | None) -> str | None:
    """
    入出金 1 件を粗利のどちらに入れるか。"in"（入金として足す）／"out"（出金として引く）／None（含めない）。
    affects_profit: 入出金項目を選んで登録したときの「粗利に含める」。None なら項目なし（種別の既定）。
    日払い・途中回収・釣銭補充は、項目の有無に関わらず含めない（人件費の二重計上・現金の移動だから）。
    """
    if kind in PROFIT_EXCLUDED_KINDS:
        return None
    if affects_profit is False:
        return None
    if affects_profit is None and kind not in PROFIT_IN_KINDS + PROFIT_OUT_KINDS:
        return None
    return "in" if kind in INFLOW_KINDS else "out"


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
    for r in (CashMovement.objects.filter(store=store, is_voided=False, business_day__date__gte=f, business_day__date__lte=t)
              .values("business_day__date", "kind", "affects_profit").annotate(s=Sum("amount"))):
        row = days.get(r["business_day__date"])
        if row is None:
            continue
        side = movement_profit_side(r["kind"], r["affects_profit"])
        if side == "in":
            row["income"] += r["s"] or ZERO
        elif side == "out":
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


def movement_json(m) -> dict:
    """入出金 1 件の表示用。項目を選んで登録したものは項目名を、そうでなければ種別の名前を「項目」として出す。"""
    return {
        "id": m.id, "kind": m.kind, "kind_label": m.get_kind_display(), "item_name": m.item_name, "label": m.item_name or m.get_kind_display(),
        "direction": "in" if m.is_inflow else "out", "amount": str(m.amount),
        "reason": m.reason, "employee_name": m.employee.display_name if m.employee_id else "", "occurred_at": m.occurred_at.isoformat(),
        "is_voided": m.is_voided, "void_reason": m.void_reason, "in_profit": movement_profit_side(m.kind, m.affects_profit) is not None,
    }


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
            moves.append(movement_json(m))
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

# ---------------------------------------------------------------------------
# M3: キャスト・スタッフ集計（報酬）
# ---------------------------------------------------------------------------
# 報酬は新しく計算しない。既存の結果（勤怠×時給 = shift_wage、確定したバック = BackEntry、支払い = EmployeePayment）を集めるだけ。
#   総支給額   = 時間報酬 ＋ バック計（調整を含む）      … staff.payroll.compute_statement の gross_amount と同じ
#   残り支給額 = 総支給額 − 日払いなどの支払い − 源泉所得税 − その他控除   … 同 balance_amount と同じ
PRODUCT_BACK_GROUPS = {"drink": "drink", "cast_drink": "drink", "bottle": "bottle", "shot": "bottle", "food": "food"}
PAY_MONEY_KEYS = (
    "wage", "back_nomination", "back_jounai", "back_douhan", "back_yobi_subtotal", "back_douhan_subtotal",
    "back_drink", "back_bottle", "back_food", "back_other", "back_total", "bonus", "minus", "gross",
    "paid", "withheld", "deducted", "balance",
    "sales_drink", "sales_bottle", "sales_food", "order_subtotal",
    "yobi_subtotal", "yobi_total", "douhan_subtotal", "douhan_total",
)
PAY_NUMBER_KEYS = ("minutes", "days", "qty_drink", "qty_bottle", "qty_food", "nomination_count", "jounai_count", "douhan_count",
                   "nomination_groups", "jounai_groups", "douhan_groups")


def _empty_pay() -> dict:
    row = {k: ZERO for k in PAY_MONEY_KEYS}
    row.update({k: Decimal("0") for k in PAY_NUMBER_KEYS})
    row["shifts"] = []
    return row


def pay_rows(store, f: date, t: date, employee_ids=None) -> dict[tuple[int, date], dict]:
    """（従業員, 営業日）ごとの報酬の内訳。月の集計はこれを足すだけなので、月の合計は必ず日別の合計と一致する。"""
    from apps.staff.models import BackEntry, EmployeePayment, Shift
    from apps.staff.payroll import shift_minutes, shift_wage

    rows: dict[tuple[int, date], dict] = {}

    def row(eid: int, d: date) -> dict:
        return rows.setdefault((eid, d), _empty_pay())

    def want(eid) -> bool:
        return employee_ids is None or eid in employee_ids

    rng = dict(business_date__gte=f, business_date__lte=t)
    for sh in Shift.objects.filter(store=store, **rng).select_related("employee", "store").prefetch_related("breaks").order_by("clock_in"):
        if not want(sh.employee_id):
            continue
        minutes = shift_minutes(sh)
        wage, amount = shift_wage(sh, minutes)
        r = row(sh.employee_id, sh.business_date)
        r["minutes"] += minutes
        r["wage"] += amount
        r["shifts"].append({"clock_in": sh.clock_in.isoformat(), "clock_out": sh.clock_out.isoformat() if sh.clock_out else None, "minutes": minutes,
                            "hourly_wage": str(wage) if wage is not None else None, "amount": str(amount)})
    for (eid, d), r in rows.items():
        r["days"] = Decimal(1) if r["shifts"] else Decimal(0)

    # 確定したバック（打消しの負のエントリも含めて合計する = 既存の集計と同じ）
    for e in BackEntry.objects.filter(store=store, status="confirmed", **rng).select_related("order_line"):
        if not want(e.employee_id):
            continue
        r = row(e.employee_id, e.business_date)
        r["back_total"] += e.amount
        if e.kind == "adjustment":
            if e.amount >= 0:
                r["bonus"] += e.amount
            else:
                r["minus"] += -e.amount
            continue
        if e.kind in ("nomination", "jounai", "douhan"):
            r[f"back_{e.kind}"] += e.amount
        elif e.kind in ("yobi_subtotal", "douhan_subtotal"):
            r[f"back_{e.kind}"] += e.amount
        else:
            line = e.order_line
            group = PRODUCT_BACK_GROUPS.get(line.category_kind if line is not None else "", "other")
            r[f"back_{group}"] += e.amount
            # 売上・本数は、元のエントリ（打消しでないもの・打ち消されていないもの）の明細から数える
            if group != "other" and line is not None and e.reversal_of_id is None and line.status == "active":
                r[f"sales_{group}"] += line.amount
                r[f"qty_{group}"] += line.quantity
                r["order_subtotal"] += line.amount

    for p in EmployeePayment.objects.filter(store=store, is_voided=False, **rng):
        if not want(p.employee_id):
            continue
        r = row(p.employee_id, p.business_date)
        r["paid"] += p.amount
        r["withheld"] += p.withholding_amount
        r["deducted"] += p.deduction_amount

    # 伝票ごとのキャスト売上（呼び・同伴）。伝票の営業日に計上
    settings = report_settings(store)
    for v in visits_for_reports(store, f, t):
        if v.status == "cancelled":
            continue
        s = slip_summary(v, settings)
        for eid, vals in s["cast"].items():
            if not want(eid):
                continue
            r = row(eid, v.business_date)
            for k in ("yobi_subtotal", "yobi_total", "douhan_subtotal", "douhan_total", "nomination_count", "jounai_count", "douhan_count"):
                r[k] += vals[k]
            for kind in ("nomination", "jounai", "douhan"):
                if vals[f"{kind}_count"] > 0:
                    r[f"{kind}_groups"] += 1

    for r in rows.values():
        r["gross"] = r["wage"] + r["back_total"]
        r["balance"] = r["gross"] - r["paid"] - r["withheld"] - r["deducted"]
    return rows


def sum_pay(rows: list[dict]) -> dict:
    total = _empty_pay()
    for r in rows:
        for k in PAY_MONEY_KEYS + PAY_NUMBER_KEYS:
            total[k] += r[k]
    return total


def pay_json(r: dict, *, with_shifts: bool = False) -> dict:
    out = {k: str(r[k]) for k in PAY_MONEY_KEYS}
    out.update({k: str(r[k]) for k in PAY_NUMBER_KEYS})
    minutes = int(r["minutes"])
    out["minutes"] = minutes
    # 平均時給 = 時間報酬 ÷ 勤務時間（表示用。四捨五入）
    out["avg_hourly"] = str(round_yen(r["wage"] * 60 / minutes, "half_up")) if minutes else None
    # 給率（キャスト）= 総支給額 ÷ 呼び小計
    pr = ratio_percent(r["gross"], r["yobi_subtotal"], 1)
    out["pay_ratio"] = str(pr) if pr is not None else None
    if with_shifts:
        out["shifts"] = r["shifts"]
    return out


def people_report(store, year: int, month: int, *, cast: bool) -> dict:
    """キャスト（または スタッフ）の月次の集計表。1 行 = 1 人。合計行つき。"""
    from apps.staff.models import Employee

    f, t = month_range(year, month)
    roles = store_roles(store)
    rows = pay_rows(store, f, t)
    by_emp: dict[int, list[dict]] = {}
    for (eid, _d), r in rows.items():
        by_emp.setdefault(eid, []).append(r)
    # 表に出す人: この店舗に配属中の人 ＋ その月に勤怠・バック・支払い・売上があった人
    ids = set(by_emp) | {eid for eid in roles}
    people = []
    for emp in Employee.objects.filter(id__in=ids, company=store.company).order_by("display_name", "id"):
        is_cast = roles.get(emp.id) == "cast"
        if is_cast != cast:
            continue
        if emp.id not in by_emp and not emp.is_active:
            continue
        total = sum_pay(by_emp.get(emp.id, []))
        people.append({"employee_id": emp.id, "employee_name": emp.display_name, "is_active": emp.is_active, "role": roles.get(emp.id) or "staff",
                       **pay_json(total), "_raw": total})
    grand = sum_pay([p.pop("_raw") for p in people])
    return {"month": f"{year}-{month:02d}", "from": f.isoformat(), "to": t.isoformat(), "rows": people, "totals": pay_json(grand),
            "settings": report_settings(store)}


def person_report(store, employee, year: int, month: int) -> dict:
    """1 人の報酬支払明細（月）と日別表。月の合計 = 日別の合計。"""
    f, t = month_range(year, month)
    rows = pay_rows(store, f, t, employee_ids={employee.id})
    days = sorted(((d, r) for (_e, d), r in rows.items()), key=lambda x: x[0])
    total = sum_pay([r for _d, r in days])
    roles = store_roles(store)
    return {
        "month": f"{year}-{month:02d}", "from": f.isoformat(), "to": t.isoformat(),
        "employee": {"id": employee.id, "name": employee.display_name, "full_name": employee.full_name, "role": roles.get(employee.id) or "staff"},
        "store_name": store.name,
        "totals": pay_json(total),
        "days": [{"date": d.isoformat(), **pay_json(r, with_shifts=True)} for d, r in days],
        "settings": report_settings(store),
    }

# ---------------------------------------------------------------------------
# M4: タグ集計・商品集計
# ---------------------------------------------------------------------------
TAG_NOTE = "1 つの伝票に複数のタグがあるときは、タグごとにその伝票を重複して数えます。そのため、タグの行を足しても店舗全体の合計とは一致しないことがあります。"
TAG_KEYS = ("subtotal", "cash", "card", "receivable", "total")


def tag_report(store, f: date, t: date, group_by: str = "tag") -> dict:
    """
    タグ集計。タグごとの 件数・客数・小計・現金・カード・売掛・販売額（= 確定した会計の請求額）。
    group_by: tag（期間の合計）／day（営業日 × タグ）／month（月 × タグ）。取消済みの伝票は数えない。
    """
    settings = report_settings(store)
    buckets: dict[tuple[str, int], dict] = {}
    tagged_slips = 0
    for v in visits_for_reports(store, f, t):
        if v.status == "cancelled":
            continue
        tags = [vt.tag for vt in v.visit_tags.all()]
        if not tags:
            continue
        tagged_slips += 1
        s = slip_summary(v, settings)
        period = "" if group_by == "tag" else v.business_date.isoformat() if group_by == "day" else v.business_date.strftime("%Y-%m")
        for tag in tags:
            b = buckets.setdefault((period, tag.id), {"period": period, "tag_id": tag.id, "tag_name": tag.name, "color": tag.color, "sort": tag.sort_order,
                                                      "count": 0, "guests": 0, **{k: ZERO for k in TAG_KEYS}})
            b["count"] += 1
            b["guests"] += v.guest_count
            for k in TAG_KEYS:
                b[k] += s[k]
    rows = sorted(buckets.values(), key=lambda b: (b["period"], b["sort"], b["tag_id"]))
    out = [{**{k: (str(v) if isinstance(v, Decimal) else v) for k, v in b.items() if k != "sort"}} for b in rows]
    totals = {"count": sum(b["count"] for b in rows), "guests": sum(b["guests"] for b in rows), **{k: str(sum((b[k] for b in rows), ZERO)) for k in TAG_KEYS}}
    return {"from": f.isoformat(), "to": t.isoformat(), "group_by": group_by, "rows": out, "totals": totals, "tagged_slips": tagged_slips, "note": TAG_NOTE}


ITEM_NOTE = (
    "会計が確定した商品の明細だけを数えます（取消した明細・未会計の明細は含みません）。日付は会計の営業日です。"
    "キャスト別は、明細の担当（バック対象）に計上します。担当が複数（折半）のときは、割合の指定ならその割合で、"
    "金額の指定（手で配分したバック）なら人数で均等に割って（円未満切り捨て）各キャストに計上します。"
)
NO_CAST = 0  # 担当なし（お客様の注文など）


def split_line_for_casts(amount: Decimal, quantity: Decimal, targets: list[tuple[int, str, Decimal]]) -> dict[int, tuple[Decimal, Decimal]]:
    """
    商品の明細 1 行を、担当のキャストに配分する（集計用。バックの金額とは別）。
    targets: [(employee_id, share_type, share_value)]。戻り値: {employee_id: (金額, 数量)}。
      percent: 金額・数量 × 割合       fixed（手で配分）: 金額 ÷ 人数（円未満切り捨て）、数量 ÷ 人数   例: 10,000 円を 3 人 → 3,333 円ずつ
    """
    if not targets:
        return {NO_CAST: (D(amount), D(quantity))}
    q2 = Decimal("0.01")
    out: dict[int, tuple[Decimal, Decimal]] = {}
    n = len(targets)
    for eid, share_type, value in targets:
        if share_type == "percent":
            a = round_yen(D(amount) * D(value) / 100, "floor")
            q = (D(quantity) * D(value) / 100).quantize(q2)
        else:
            a = round_yen(D(amount) / n, "floor")
            q = (D(quantity) / n).quantize(q2)
        pa, pq = out.get(eid, (ZERO, ZERO))
        out[eid] = (pa + a, pq + q)
    return out


def item_report(store, f: date, t: date) -> dict:
    """商品集計: 日付 × 商品の個数、キャスト × 商品の個数と金額。"""
    from apps.sales.models import OrderLine

    lines = (OrderLine.objects.filter(store=store, line_type="product", status="active", checkout__status="confirmed",
                                      checkout__business_date__gte=f, checkout__business_date__lte=t)
             .select_related("checkout", "product__category").prefetch_related("back_targets__employee").order_by("id"))
    products: dict[tuple, dict] = {}
    dates: set[str] = set()
    casts: dict[int, dict] = {}
    for l in lines:
        d = l.checkout.business_date.isoformat()
        dates.add(d)
        key = (l.product_id, l.name)
        p = products.setdefault(key, {"product_id": l.product_id, "name": l.name,
                                      "category_name": l.product.category.name if l.product_id and l.product else "", "category_kind": l.category_kind,
                                      "quantity": ZERO, "amount": ZERO, "by_date": {}})
        p["quantity"] += l.quantity
        p["amount"] += l.amount
        p["by_date"][d] = p["by_date"].get(d, ZERO) + l.quantity
        targets = [(bt.employee_id, bt.share_type, bt.share_value) for bt in l.back_targets.all()]
        names = {bt.employee_id: bt.employee.display_name for bt in l.back_targets.all()}
        for eid, (a, q) in split_line_for_casts(l.amount, l.quantity, targets).items():
            c = casts.setdefault(eid, {"employee_id": eid or None, "employee_name": names.get(eid, "担当なし（お客様の注文など）"), "quantity": ZERO, "amount": ZERO, "items": {}})
            c["quantity"] += q
            c["amount"] += a
            it = c["items"].setdefault(key, {"product_id": l.product_id, "name": l.name, "quantity": ZERO, "amount": ZERO})
            it["quantity"] += q
            it["amount"] += a
    order = sorted(products.values(), key=lambda p: (-p["amount"], p["name"]))
    product_rows = [{**{k: v for k, v in p.items() if k not in ("quantity", "amount", "by_date")}, "quantity": str(p["quantity"]), "amount": str(p["amount"]),
                     "by_date": {d: str(q) for d, q in p["by_date"].items()}} for p in order]
    cast_rows = []
    for c in sorted(casts.values(), key=lambda c: (c["employee_id"] is None, -c["amount"])):
        cast_rows.append({"employee_id": c["employee_id"], "employee_name": c["employee_name"], "quantity": str(c["quantity"]), "amount": str(c["amount"]),
                          "items": [{**it, "quantity": str(it["quantity"]), "amount": str(it["amount"])} for it in sorted(c["items"].values(), key=lambda i: -i["amount"])]})
    return {
        "from": f.isoformat(), "to": t.isoformat(), "dates": sorted(dates), "products": product_rows, "casts": cast_rows,
        "totals": {"quantity": str(sum((p["quantity"] for p in products.values()), ZERO)), "amount": str(sum((p["amount"] for p in products.values()), ZERO)),
                   "by_date": {d: str(sum((p["by_date"].get(d, ZERO) for p in products.values()), ZERO)) for d in sorted(dates)}},
        "note": ITEM_NOTE,
    }


# ---------------------------------------------------------------------------
# M5: お客様分析
# ---------------------------------------------------------------------------
RANK_MONTHS = 3
CUSTOMER_NOTE = (
    "来店・会計はこの店舗の分だけを数えます（会社内で共有されている他店舗のお客様も、数えるのはこの店舗への来店だけです）。"
    "取消した伝票は数えません。金額は確定した会計の合計、単価は 金額 ÷ 来店回数、平均滞在は退店済みの伝票の平均です。"
    "本指名・場内・同伴は、その明細がある伝票の数です。ランクは直近 3 か月の来店回数で決まります。"
)


def months_before(d: date, months: int) -> date:
    """d の months か月前の同じ日（その月に同じ日が無ければ月末）。例: 5/31 の 3 か月前 → 2/28"""
    import calendar

    y, m = d.year, d.month - months
    while m <= 0:
        y, m = y - 1, m + 12
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def clean_rank_thresholds(raw) -> list[dict]:
    """店舗設定のランクの一覧を、来店回数の多い順に整える。形の合わない項目は捨てる。"""
    out = []
    for item in raw if isinstance(raw, list) else []:
        try:
            out.append({"label": str(item["label"]), "min_visits": int(item["min_visits"])})
        except (KeyError, TypeError, ValueError):
            continue
    return sorted(out, key=lambda x: -x["min_visits"])


def customer_rank(visits: int, thresholds: list[dict]) -> str:
    """直近の来店回数からランクを決める。thresholds は回数の多い順。どれにも届かなければ空（ランクなし）。"""
    for th in thresholds:
        if visits >= th["min_visits"]:
            return th["label"]
    return ""


def customer_report(store, today: date) -> dict:
    """
    お客様分析。この店舗から見られる顧客（customers.services.visible_customers）ごとに、この店舗への来店を数える。
    別会社の顧客・共有されていない他店舗の顧客は含めない。他店舗での来店・会計は数えない。
    """
    from django.db.models import Prefetch

    from apps.customers.models import BottleKeep
    from apps.customers.services import visible_customers
    from apps.sales.models import OrderLine, Visit

    settings = report_settings(store)
    thresholds = clean_rank_thresholds(getattr(getattr(store, "settings", None), "customer_rank_thresholds", []))
    since = months_before(today, RANK_MONTHS)
    customers = list(visible_customers(store).filter(is_active=True).select_related("store", "assigned_employee"))
    ids = [c.id for c in customers]
    keeps: dict[int, int] = {}
    for cid in BottleKeep.objects.filter(store=store, customer_id__in=ids, status="active").values_list("customer_id", flat=True):
        keeps[cid] = keeps.get(cid, 0) + 1
    money_keys = ("total", "cash", "card", "receivable")
    count_keys = ("visits", "visits_recent", "guests", "nomination", "jounai", "douhan")
    acc = {cid: {**{k: ZERO for k in money_keys}, **{k: 0 for k in count_keys}, "first": None, "last": None, "minutes": 0, "closed": 0} for cid in ids}
    visits = Visit.objects.filter(store=store, customer_id__in=ids).exclude(status="cancelled").select_related("table", "customer").prefetch_related(
        Prefetch("lines", queryset=OrderLine.objects.select_related("checkout").prefetch_related("back_targets__employee")),
        "checkouts__payments", "checkouts__refunds",
    )
    for v in visits:
        a = acc[v.customer_id]
        s = slip_summary(v, settings)
        a["visits"] += 1
        a["guests"] += v.guest_count
        if since < v.business_date <= today:
            a["visits_recent"] += 1
        a["first"] = v.business_date if a["first"] is None else min(a["first"], v.business_date)
        a["last"] = v.business_date if a["last"] is None else max(a["last"], v.business_date)
        if s["minutes"] is not None:
            a["minutes"] += s["minutes"]
            a["closed"] += 1
        for k in money_keys:
            a[k] += s[k]
        for k in ("nomination", "jounai", "douhan"):
            if s["names_by_kind"][k]:
                a[k] += 1
    rows = []
    for c in customers:
        a = acc[c.id]
        unit = per_unit(a["total"], a["visits"])
        rows.append({
            "id": c.id, "name": c.display_name, "store_name": c.store.name, "own_store": c.store_id == store.id,
            "assigned_employee_name": c.assigned_employee.display_name if c.assigned_employee_id else "",
            "keep_count": keeps.get(c.id, 0), "rank": customer_rank(a["visits_recent"], thresholds),
            "visits": a["visits"], "visits_recent": a["visits_recent"], "guests": a["guests"],
            "first_visit": a["first"].isoformat() if a["first"] else None, "last_visit": a["last"].isoformat() if a["last"] else None,
            "avg_minutes": round(a["minutes"] / a["closed"]) if a["closed"] else None,
            "total": str(a["total"]), "unit_price": str(unit) if unit is not None else None,
            "cash": str(a["cash"]), "card": str(a["card"]), "receivable": str(a["receivable"]),
            "nomination": a["nomination"], "jounai": a["jounai"], "douhan": a["douhan"],
        })
    # 最終来店日の新しい順。来店の無いお客様は最後
    rows.sort(key=lambda r: (r["last_visit"] is None, -date.fromisoformat(r["last_visit"]).toordinal() if r["last_visit"] else 0, r["name"]))
    t_visits = sum(r["visits"] for r in rows)
    t_total = sum((a["total"] for a in acc.values()), ZERO)
    t_unit = per_unit(t_total, t_visits)
    totals = {
        "customers": len(rows), "keep_count": sum(r["keep_count"] for r in rows),
        **{k: sum(r[k] for r in rows) for k in ("visits", "visits_recent", "guests", "nomination", "jounai", "douhan")},
        **{k: str(sum((a[k] for a in acc.values()), ZERO)) for k in money_keys},
        "unit_price": str(t_unit) if t_unit is not None else None,
    }
    return {
        "as_of": today.isoformat(), "rank_since": since.isoformat(), "rank_months": RANK_MONTHS, "rank_thresholds": thresholds,
        "sharing_enabled": bool(store.company.customer_sharing_enabled), "rows": rows, "totals": totals, "note": CUSTOMER_NOTE,
    }


# ---------------------------------------------------------------------------
# M6: 履歴（入出金、伝票の削除）
# ---------------------------------------------------------------------------
def cash_history(store, year: int, month: int) -> dict:
    """入出金の履歴（月）。項目ごとの合計と明細。取り消した入出金は明細に出すが、合計には入れない。"""
    from apps.cash.models import CashMovement

    f, t = month_range(year, month)
    groups: dict[tuple[str, str], dict] = {}
    rows = []
    totals = {"in": ZERO, "out": ZERO, "profit_in": ZERO, "profit_out": ZERO}
    qs = (CashMovement.objects.filter(store=store, business_day__date__gte=f, business_day__date__lte=t)
          .select_related("employee", "business_day", "performed_by").order_by("business_day__date", "occurred_at", "id"))
    for m in qs:
        j = movement_json(m)
        j["date"] = m.business_day.date.isoformat()
        j["performed_by_name"] = str(m.performed_by) if m.performed_by_id else ""
        rows.append(j)
        if m.is_voided:
            continue
        side = movement_profit_side(m.kind, m.affects_profit)
        g = groups.setdefault((j["direction"], j["label"], side is not None), {
            "direction": j["direction"], "label": j["label"], "is_item": bool(m.item_name), "count": 0, "amount": ZERO, "in_profit": side is not None})
        g["count"] += 1
        g["amount"] += m.amount
        totals[j["direction"]] += m.amount
        if side is not None:
            totals[f"profit_{side}"] += m.amount
    summary = [{**g, "amount": str(g["amount"])} for g in sorted(groups.values(), key=lambda g: (g["direction"] != "in", -g["amount"], g["label"]))]
    return {"month": f"{year}-{month:02d}", "from": f.isoformat(), "to": t.isoformat(), "summary": summary, "rows": rows,
            "totals": {k: str(v) for k, v in totals.items()}, "profit_kind_note": PROFIT_KIND_NOTE}


DELETION_ACTIONS = {
    "visit.cancel": "伝票の取消",
    "checkout.delete": "会計の取消（記録ごと削除）",
    "checkout.void": "会計の取消",
    "checkout.refund": "返金",
    "line.cancel": "明細の取消",
    "line.cancel.batch": "明細の取消（まとめて）",
}
DELETION_NOTE = (
    "伝票・会計・明細を取り消した操作の記録です（操作履歴から作っています）。印刷の履歴はありません。"
    "「会計の取消（記録ごと削除）」は、会計の記録が画面・集計から消えるため、金額はこの履歴にだけ残ります。"
)


def deletion_history(store, f: date, t: date) -> dict:
    """
    伝票の削除履歴。
    - cancelled: 期間（営業日）内の取消済みの伝票
    - logs: 期間（操作した日。営業日の翌朝までを含む）内の、取消・返金の操作履歴（AuditLog）
    """
    from datetime import datetime, time, timedelta

    from django.utils import timezone

    from apps.core.models import AuditLog
    from apps.sales.models import Visit

    cancelled = []
    for v in (Visit.objects.filter(store=store, status="cancelled", business_date__gte=f, business_date__lte=t)
              .select_related("table", "customer").order_by("business_date", "slip_number")):
        cancelled.append({
            "id": v.id, "public_id": v.public_id, "slip_number": v.slip_number, "business_date": v.business_date.isoformat(), "table_name": v.table.name,
            "guest_count": v.guest_count, "customer": (v.customer.display_name if v.customer_id and v.customer else v.customer_name) or "",
            "opened_at": v.opened_at.isoformat(), "closed_at": v.closed_at.isoformat() if v.closed_at else None, "cancel_reason": v.cancel_reason,
        })
    tz = timezone.get_current_timezone()
    start = datetime.combine(f, time.min, tzinfo=tz)
    end = datetime.combine(t + timedelta(days=2), time.min, tzinfo=tz)
    logs = list(AuditLog.objects.filter(store=store, action__in=list(DELETION_ACTIONS), created_at__gte=start, created_at__lt=end)
                .select_related("user").order_by("-id")[:1000])
    from apps.sales.models import Checkout

    # 操作の対象から伝票をたどる（伝票そのもの／会計 → 伝票。記録ごと消した会計は、操作履歴に残した伝票 ID から）
    co_ids = {int(l.target_id) for l in logs if l.target_type == "Checkout" and l.target_id.isdigit()}
    co_visit = dict(Checkout.objects.filter(store=store, id__in=co_ids).values_list("id", "visit_id"))

    def visit_id_of(l, ch):
        if not l.target_id.isdigit():
            return None
        if l.target_type == "Visit":
            return int(l.target_id)
        if l.target_type == "Checkout":
            raw = ch.get("visit_id") or co_visit.get(int(l.target_id))
            return int(raw) if raw else None
        return None

    pairs = [(l, l.changes if isinstance(l.changes, dict) else {}) for l in logs]
    visits = {v.id: v for v in Visit.objects.filter(store=store, id__in={visit_id_of(l, ch) for l, ch in pairs} - {None}).select_related("table")}
    out = []
    for l, ch in pairs:
        v = visits.get(visit_id_of(l, ch))
        amount = ch.get("total") if ch.get("total") is not None else ch.get("amount")
        detail = ch.get("name") or ""
        if l.action == "line.cancel.batch":
            detail = f"{len(ch.get('line_ids') or [])} 件"
        elif l.action == "checkout.delete":
            detail = "お客様に全額返す" if ch.get("mode") == "refund" else "打ち間違い（伝票を会計前に戻す）"
        out.append({
            "id": l.id, "at": l.created_at.isoformat(), "action": l.action, "action_label": DELETION_ACTIONS[l.action],
            "user_name": str(l.user) if l.user_id else "", "visit_id": v.id if v else None, "public_id": v.public_id if v else "",
            "table_name": v.table.name if v else "", "business_date": v.business_date.isoformat() if v else str(ch.get("business_date") or ""),
            "detail": str(detail), "amount": str(amount) if amount is not None else None, "reason": str(ch.get("reason") or ""),
        })
    return {"from": f.isoformat(), "to": t.isoformat(), "cancelled": cancelled, "logs": out, "note": DELETION_NOTE}
