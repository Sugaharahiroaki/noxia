"""
営業日・現金精算のサービス。

理論現金 = 開始時釣銭 + 現金売上 + 未収回収(現金) + その他現金入金 - 現金返金 - 現金経費 - 日払い - その他出金 - 途中回収
入金用取り分け額 = 実際の現金 - 翌日釣銭準備金（負なら不足・補充必要額）
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from apps.core.business_date import business_date_now
from apps.core.exceptions import ApiError
from apps.core.money import D, ZERO

from .models import BankDeposit, BusinessDay, BusinessDayStatus, CashMovement, CashMovementKind, ClosingRevision

DENOMINATIONS = [10000, 5000, 2000, 1000, 500, 100, 50, 10, 5, 1]


def _sum(qs, field="amount") -> Decimal:
    return qs.aggregate(s=Sum(field))["s"] or ZERO


def current_open_day(store) -> BusinessDay | None:
    return BusinessDay.objects.filter(store=store, status=BusinessDayStatus.OPEN).order_by("-date").first()


def previous_closed_day(store, before: date) -> BusinessDay | None:
    return BusinessDay.objects.filter(store=store, status=BusinessDayStatus.CLOSED, date__lt=before).order_by("-date").first()


def require_open_business_day(store) -> BusinessDay:
    """
    レジが開いている（開店処理済み）営業日を返す。開いていなければ入店・会計・入出金はできない（POS と同じ運用）。
    営業日を開くのは「開店する」操作（get_or_open_business_day）だけ。
    """
    day = current_open_day(store)
    if day is None:
        raise ApiError(
            "register_not_open",
            "開店（レジ開け）がまだです。釣銭準備金を確認して開店してから、入店・会計をしてください。",
            status=409,
        )
    return day


@transaction.atomic
def get_or_open_business_day(store, user, *, opening_float: Decimal | None = None, for_date: date | None = None) -> BusinessDay:
    """
    営業中の営業日があればそれを返す（未締めの前営業日も含む）。なければ本日の営業日を開く。
    釣銭準備金は指定がなければ前営業日の翌日準備金、それもなければ店舗設定の既定値を使う。
    """
    day = current_open_day(store)
    if day is not None:
        if opening_float is not None and day.opening_float != D(opening_float):
            day.opening_float = D(opening_float)
            day.opening_float_carried = False
            day.save(update_fields=["opening_float", "opening_float_carried"])
        return day
    target = for_date or business_date_now(store)
    existing = BusinessDay.objects.filter(store=store, date=target).first()
    if existing is not None:
        # 同じ営業日が締め済み: 再開は許可せず、翌日扱いにもしない（締め修正を使う）
        raise ApiError(
            "day_closed",
            f"{target.isoformat()} の営業日は締め済みです。続けて営業する場合は「開店・日締め・現金」で締め済み営業日を再開してください（権限・再認証が必要）。",
            status=409,
        )
    carried = False
    if opening_float is None:
        prev = previous_closed_day(store, target)
        if prev is not None and prev.next_day_float is not None:
            opening_float = prev.next_day_float
            carried = True
        else:
            st = getattr(store, "settings", None)
            opening_float = st.default_opening_float if st else ZERO
    return BusinessDay.objects.create(
        store=store, date=target, status=BusinessDayStatus.OPEN, opened_at=timezone.now(), opened_by=user,
        opening_float=D(opening_float), opening_float_carried=carried,
    )


def day_summary(day: BusinessDay) -> dict:
    """営業日の集計（締め前はリアルタイム計算、締め後も同じ計算式で再計算して表示する）。"""
    from apps.sales.models import Checkout, Payment, ReceivableCollection, Refund, Visit

    store = day.store
    valid_payments = Payment.objects.filter(store=store, business_date=day.date, checkout__status="confirmed", status="valid")
    # 表示用の売上（取消済みの会計は除く）
    cash_sales = _sum(valid_payments.filter(method="cash"))
    card_sales = _sum(valid_payments.filter(method="card"))
    receivable_sales = _sum(valid_payments.filter(method="receivable"))
    refunds = Refund.objects.filter(store=store, business_date=day.date)
    cash_refunds = _sum(refunds.filter(method="cash"))
    card_refunds = _sum(refunds.filter(method="card"))
    # 理論現金の計算では「この営業日にレジで受け取った現金（あとで取り消したものを含む）」を足し、現金の返金を引く。
    # 取消済みの支払いを売上から外したうえで返金も引くと二重に減るため（2026-11 修正）
    cash_received = _sum(Payment.objects.filter(store=store, business_date=day.date, method="cash").exclude(status="voided"))
    collections = ReceivableCollection.objects.filter(store=store, business_date=day.date)
    coll_cash = _sum(collections.filter(method="cash"))
    coll_card = _sum(collections.filter(method="card"))
    moves = CashMovement.objects.filter(business_day=day, is_voided=False)
    by_kind = {k: _sum(moves.filter(kind=k)) for k, _ in CashMovementKind.choices}
    theoretical = (
        D(day.opening_float) + cash_received + coll_cash + by_kind["other_in"] + by_kind["float_adjust_in"]
        - cash_refunds - by_kind["expense"] - by_kind["daily_pay"] - by_kind["other_out"] - by_kind["mid_collection"]
    )
    checkouts = Checkout.objects.filter(store=store, business_date=day.date, status="confirmed")
    guest_count = Visit.objects.filter(store=store, business_date=day.date).exclude(status="cancelled").aggregate(s=Sum("guest_count"))["s"] or 0
    sales_total = _sum(checkouts, "total")
    deposits = _sum(BankDeposit.objects.filter(business_day=day))
    return {
        "opening_float": str(D(day.opening_float)),
        "cash_sales": str(cash_sales),
        "card_sales": str(card_sales),
        "receivable_sales": str(receivable_sales),
        "sales_total": str(sales_total),
        "checkout_count": checkouts.count(),
        "guest_count": guest_count,
        "receivable_collected_cash": str(coll_cash),
        "receivable_collected_card": str(coll_card),
        "cash_refunds": str(cash_refunds),
        "card_refunds": str(card_refunds),
        "cash_in_other": str(by_kind["other_in"] + by_kind["float_adjust_in"]),
        "cash_out_expense": str(by_kind["expense"]),
        "cash_out_daily_pay": str(by_kind["daily_pay"]),
        "cash_out_other": str(by_kind["other_out"]),
        "cash_out_mid_collection": str(by_kind["mid_collection"]),
        "theoretical_cash": str(theoretical),
        "bank_deposits_total": str(deposits),
        # 本日の手残り（目安）= 売上 − 返金 − 従業員への日払い − 現金経費。仕入・家賃などの固定費は含まない
        "net_estimate": str(sales_total - cash_refunds - card_refunds - by_kind["daily_pay"] - by_kind["expense"]),
    }


def open_visits_for(store) -> list[dict]:
    """未会計（営業中）の伝票。日締めはこれが0件でないとできない。"""
    from apps.sales.models import Visit

    return [
        {"visit_id": v.id, "slip_number": v.slip_number, "table_name": v.table.name}
        for v in Visit.objects.filter(store=store, status="open").select_related("table").order_by("table__sort_order", "slip_number")
    ]


def precheck(day: BusinessDay) -> list[dict]:
    """締め前の確認事項。"""
    from apps.sales.models import Visit
    from apps.staff.models import Shift

    warnings = []
    open_visits = Visit.objects.filter(store=day.store, status="open").count()
    if open_visits:
        warnings.append({"code": "open_visits", "blocking": True, "message": f"未会計の伝票が {open_visits} 件あります。会計または取消をするまで締められません。"})
    from apps.staff import payroll

    due = [
        p for p in (payroll.daily_payout(day.store, e, day.date, include_rate=False) for e in payroll.daily_payout_employees(day.store, day.date))
        if p["status"] in ("ready", "partial", "missing_wage")
    ]
    if due:
        names = "、".join(f"{p['employee_name']}" for p in due[:6])
        warnings.append({"code": "unpaid_payouts", "message": f"日払いがまだ渡されていない人が {len(due)}名 います（{names}）。渡すとレジの現金出金に入ります。"})
    from apps.terminals.models import TerminalPayment

    attention = TerminalPayment.objects.filter(store=day.store, status="needs_attention", resolved_at__isnull=True).count()
    if attention:
        warnings.append({"code": "terminal_attention", "message": f"決済端末では承認済みなのに会計にできなかった決済が {attention} 件あります。会計画面の「要確認」で対応してください。"})
    open_shifts = Shift.objects.filter(store=day.store, clock_out__isnull=True).count()
    if open_shifts:
        warnings.append({"code": "open_shifts", "message": f"退勤していない勤怠が {open_shifts} 件あります。"})
    today = business_date_now(day.store)
    if day.date != today:
        warnings.append({"code": "date_mismatch", "message": f"この営業日（{day.date.isoformat()}）は現在の営業日（{today.isoformat()}）と異なります。前営業日が未締めのまま営業している可能性があります。"})
    if day.opening_float_carried:
        warnings.append({"code": "float_carried", "message": "釣銭準備金は前営業日の翌日準備金を引き継いでいます。実際の金額と異なる場合は修正してください。"})
    return warnings


def count_from_denominations(denoms: dict) -> Decimal:
    total = ZERO
    for k, v in (denoms or {}).items():
        try:
            face = int(k)
            n = int(v or 0)
        except (TypeError, ValueError):
            raise ApiError("validation_error", "金種別枚数の形式が正しくありません。")
        if face not in DENOMINATIONS or n < 0:
            raise ApiError("validation_error", f"金種 {k} または枚数が不正です。")
        total += Decimal(face) * Decimal(n)
    return total


@transaction.atomic
def close_business_day(day: BusinessDay, user, *, counted_cash=None, denominations=None, next_day_float, difference_reason="", note="") -> BusinessDay:
    day = BusinessDay.objects.select_for_update().get(pk=day.pk)
    if day.status == BusinessDayStatus.CLOSED:
        raise ApiError("day_closed", "この営業日は既に締め済みです。", status=409)
    open_visits = open_visits_for(day.store)
    if open_visits:
        names = "、".join(f"{v['table_name']}（伝票#{v['slip_number']}）" for v in open_visits[:6])
        raise ApiError(
            "open_visits",
            f"未会計の伝票が {len(open_visits)} 件あります: {names}。会計または伝票の取消をしてから締めてください。",
            status=409,
            details=open_visits,
        )
    if denominations:
        counted = count_from_denominations(denominations)
        if counted_cash is not None and D(counted_cash) != counted:
            raise ApiError("validation_error", "金種別枚数の合計と実際の現金が一致しません。")
        counted_cash = counted
    if counted_cash is None:
        raise ApiError("validation_error", "実際の現金を入力してください。")
    counted_cash = D(counted_cash)
    next_float = D(next_day_float)
    if next_float < 0:
        raise ApiError("validation_error", "翌日釣銭準備金は0以上で入力してください。")
    s = day_summary(day)
    theoretical = D(s["theoretical_cash"])
    difference = counted_cash - theoretical
    if difference != 0 and not (difference_reason or "").strip():
        raise ApiError("validation_error", f"現金差額 {difference:+,.0f}円 があります。差額理由を入力してください。")
    set_aside = counted_cash - next_float
    day.cash_sales = D(s["cash_sales"])
    day.card_sales = D(s["card_sales"])
    day.receivable_sales = D(s["receivable_sales"])
    day.receivable_collected_cash = D(s["receivable_collected_cash"])
    day.receivable_collected_card = D(s["receivable_collected_card"])
    day.cash_refunds = D(s["cash_refunds"])
    day.card_refunds = D(s["card_refunds"])
    day.cash_in_other = D(s["cash_in_other"])
    day.cash_out_expense = D(s["cash_out_expense"])
    day.cash_out_daily_pay = D(s["cash_out_daily_pay"])
    day.cash_out_other = D(s["cash_out_other"])
    day.cash_out_mid_collection = D(s["cash_out_mid_collection"])
    day.theoretical_cash = theoretical
    day.counted_cash = counted_cash
    day.denominations = denominations or {}
    day.difference = difference
    day.difference_reason = difference_reason or ""
    day.next_day_float = next_float
    day.set_aside_amount = set_aside if set_aside >= 0 else ZERO
    day.shortage_amount = -set_aside if set_aside < 0 else ZERO
    day.note = note or ""
    day.status = BusinessDayStatus.CLOSED
    day.closed_at = timezone.now()
    day.closed_by = user
    day.save()
    purge_old_idempotency_records()
    return day


IDEMPOTENCY_RETENTION_DAYS = 2


def purge_old_idempotency_records(days: int = IDEMPOTENCY_RETENTION_DAYS) -> int:
    """
    二重登録防止の記録（応答のコピー）は再送の間だけ必要なので、古いものを消して容量を節約する。
    レジ締めのたびに実行する（cron でも `manage.py cleanup_records` で実行できる）。
    """
    from datetime import timedelta

    from apps.core.models import IdempotencyRecord

    deleted, _ = IdempotencyRecord.objects.filter(created_at__lt=timezone.now() - timedelta(days=days)).delete()
    return deleted


CLOSING_EDITABLE = {"counted_cash", "denominations", "difference_reason", "next_day_float", "note", "opening_float"}


@transaction.atomic
def revise_closing(day: BusinessDay, user, changes: dict, reason: str) -> BusinessDay:
    day = BusinessDay.objects.select_for_update().get(pk=day.pk)
    if day.status != BusinessDayStatus.CLOSED:
        raise ApiError("not_closed", "締め済みの営業日だけ修正できます。", status=409)
    if not (reason or "").strip():
        raise ApiError("validation_error", "修正理由を入力してください。")
    before = {k: str(getattr(day, k)) if not isinstance(getattr(day, k), dict) else getattr(day, k) for k in CLOSING_EDITABLE}
    for k, v in changes.items():
        if k not in CLOSING_EDITABLE:
            continue
        if k == "denominations":
            day.denominations = v or {}
            day.counted_cash = count_from_denominations(v) if v else day.counted_cash
        elif k in ("counted_cash", "next_day_float", "opening_float"):
            setattr(day, k, D(v))
        else:
            setattr(day, k, v or "")
    # 再計算
    s = day_summary(day)
    day.theoretical_cash = D(s["theoretical_cash"])
    if day.counted_cash is not None:
        day.difference = D(day.counted_cash) - day.theoretical_cash
        set_aside = D(day.counted_cash) - D(day.next_day_float or 0)
        day.set_aside_amount = set_aside if set_aside >= 0 else ZERO
        day.shortage_amount = -set_aside if set_aside < 0 else ZERO
    day.save()
    after = {k: str(getattr(day, k)) if not isinstance(getattr(day, k), dict) else getattr(day, k) for k in CLOSING_EDITABLE}
    ClosingRevision.objects.create(business_day=day, changed_by=user, reason=reason, before=before, after=after)
    return day
