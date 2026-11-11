"""
勤務・バックに基づく支払集計（給与の法定控除・割増賃金は未対応。正式な給与計算ではない）。

- 支給対象額 = 時給 × 勤務時間（休憩除く、分単位・円未満切り捨て） + 確定バック
- 実支払額 = 期間内の EmployeePayment（日払い・前払い・締め支払い）の合計（取消分を除く）
- 精算済み = 実支払額 + 源泉所得税（預り金）+ その他控除
- 残額 = 支給対象額 - 精算済み
時給は勤務日（営業日）時点で有効な WageSetting を使う。締め後は PayStatement に固定する。
ホステス報酬の源泉徴収は accounting.services（日払いの「お渡し」で計算・記録）。
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone

from apps.core.money import D, ZERO, round_yen

from .models import BackEntry, BackEntryStatus, EmployeePayment, PayStatement, Shift, WageSetting


def wage_for(employee, store, on_date: date) -> Decimal | None:
    ws = WageSetting.objects.filter(employee=employee, store=store, effective_from__lte=on_date).order_by("-effective_from", "-id").first()
    return D(ws.hourly_wage) if ws else None


def shift_minutes(shift: Shift, now=None) -> int:
    end = shift.clock_out or now or timezone.now()
    total = int((end - shift.clock_in).total_seconds() // 60)
    for b in shift.breaks.all():
        b_end = b.ended_at or end
        total -= int((b_end - b.started_at).total_seconds() // 60)
    return max(total, 0)


def shift_wage(shift: Shift, minutes: int) -> tuple[Decimal | None, Decimal]:
    wage = wage_for(shift.employee, shift.store, shift.business_date)
    if wage is None:
        return None, ZERO
    return wage, round_yen(wage * Decimal(minutes) / Decimal(60), "floor")


def back_total(store, employee, start: date, end: date) -> Decimal:
    agg = BackEntry.objects.filter(
        store=store, employee=employee, business_date__gte=start, business_date__lte=end, status=BackEntryStatus.CONFIRMED
    ).aggregate(s=Sum("amount"))
    return agg["s"] or ZERO


def _payments_qs(store, employee, start: date, end: date, pay_period=None):
    qs = EmployeePayment.objects.filter(store=store, employee=employee, is_voided=False)
    if pay_period is not None:
        from django.db.models import Q
        return qs.filter(Q(business_date__gte=start, business_date__lte=end) | Q(pay_period=pay_period))
    return qs.filter(business_date__gte=start, business_date__lte=end)


def payments_total(store, employee, start: date, end: date, pay_period=None) -> Decimal:
    return _payments_qs(store, employee, start, end, pay_period).aggregate(s=Sum("amount"))["s"] or ZERO


def compute_statement(store, employee, start: date, end: date, pay_period=None) -> dict:
    shifts = Shift.objects.filter(store=store, employee=employee, business_date__gte=start, business_date__lte=end).prefetch_related("breaks").order_by("clock_in")
    rows = []
    total_minutes = 0
    wage_amount = ZERO
    missing_wage = False
    for s in shifts:
        minutes = shift_minutes(s)
        wage, amount = shift_wage(s, minutes)
        if wage is None:
            missing_wage = True
        total_minutes += minutes
        wage_amount += amount
        rows.append({
            "shift_id": s.id, "business_date": s.business_date.isoformat(), "clock_in": s.clock_in.isoformat(),
            "clock_out": s.clock_out.isoformat() if s.clock_out else None, "minutes": minutes,
            "hourly_wage": str(wage) if wage is not None else None, "amount": str(amount), "open": s.clock_out is None,
        })
    backs = back_total(store, employee, start, end)
    agg = _payments_qs(store, employee, start, end, pay_period).aggregate(p=Sum("amount"), w=Sum("withholding_amount"), d=Sum("deduction_amount"))
    paid = agg["p"] or ZERO
    withheld = agg["w"] or ZERO
    deducted = agg["d"] or ZERO
    gross = wage_amount + backs
    return {
        "employee_id": employee.id,
        "employee_name": employee.display_name,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "worked_minutes": total_minutes,
        "wage_amount": str(wage_amount),
        "back_amount": str(backs),
        "gross_amount": str(gross),
        "paid_amount": str(paid),
        "withheld_amount": str(withheld),
        "deducted_amount": str(deducted),
        "balance_amount": str(gross - paid - withheld - deducted),
        "missing_wage_setting": missing_wage,
        "shifts": rows,
        "notice": "勤務・バックに基づく支払集計です。ホステス報酬の源泉所得税は日払いのお渡し時に計算した額を表示します。給与の所得税・社会保険・割増賃金などの法定控除は含みません。",
    }


def daily_payout(store, employee, work_date: date, *, include_rate: bool = True, selected_deductions: list[str] | None = None) -> dict:
    """
    その日のお渡し額（日払い）。
      報酬総額   = 時給 × 勤務時間（休憩除く） + その日の確定バック
      未精算     = 報酬総額 − その日の勤務分として精算済みの額（渡した額＋源泉所得税＋その他控除。work_date で突き合わせ）
      お渡し額   = 未精算 − 源泉所得税 − その他控除（厚生費・チェックした備品代など）  ← remaining_amount
    源泉所得税・その他控除は accounting.services.payout_tax_preview で計算する（税務区分が未確認なら計算せず「要確認」、
    店舗設定で源泉所得税を差し引かない場合は計算しない）。selected_deductions: チェックした店舗の控除項目（None なら既定）。
    """
    shifts = list(
        Shift.objects.filter(store=store, employee=employee, business_date=work_date).prefetch_related("breaks").order_by("clock_in")
    )
    rows = []
    minutes_total = 0
    wage_total = ZERO
    missing_wage = False
    working = False
    on_break = False
    open_shift_id = None
    for s in shifts:
        if s.clock_out is None:
            working = True
            open_shift_id = s.id
            on_break = any(b.ended_at is None for b in s.breaks.all())
        minutes = shift_minutes(s)
        wage, amount = shift_wage(s, minutes)
        if wage is None:
            missing_wage = True
        minutes_total += minutes
        wage_total += amount
        rows.append({
            "shift_id": s.id, "clock_in": s.clock_in.isoformat(), "clock_out": s.clock_out.isoformat() if s.clock_out else None,
            "minutes": minutes, "hourly_wage": (str(wage) if wage is not None else None) if include_rate else None, "amount": str(amount),
        })
    backs = BackEntry.objects.filter(store=store, employee=employee, business_date=work_date)
    back_confirmed = backs.filter(status=BackEntryStatus.CONFIRMED).aggregate(s=Sum("amount"))["s"] or ZERO
    back_pending = backs.filter(status=BackEntryStatus.PENDING).aggregate(s=Sum("amount"))["s"] or ZERO
    payments = list(
        EmployeePayment.objects.filter(store=store, employee=employee, work_date=work_date, is_voided=False).order_by("paid_at")
    )
    paid = sum((p.amount for p in payments), ZERO)
    withheld = sum((p.withholding_amount for p in payments), ZERO)
    deducted = sum((p.deduction_amount for p in payments), ZERO)
    settled = paid + withheld + deducted
    gross = wage_total + back_confirmed
    remaining_gross = gross - settled
    if working:
        status = "working"
    elif not shifts and back_confirmed == 0 and paid == 0:
        status = "absent"
    elif missing_wage:
        status = "missing_wage"
    elif remaining_gross <= 0:
        status = "paid" if paid > 0 else "nothing"
    elif paid > 0:
        status = "partial"
    else:
        status = "ready"
    items: list[dict] = []
    tax = None
    remaining = remaining_gross
    if status != "absent":
        from apps.accounting import compensation
        from apps.accounting.services import payout_tax_preview

        items = compensation.daily_items(store, employee, work_date, wage_total)
        tax = payout_tax_preview(store, employee, work_date, items=items, gross_day=gross, remaining_gross=remaining_gross,
                                 deducted=compensation.deducted_by_code([{"deduction_items": p.deduction_items} for p in payments]),
                                 selected=selected_deductions)
        if remaining_gross > 0:
            remaining = D(tax["net_amount"])
    return {
        "employee_id": employee.id,
        "employee_name": employee.display_name,
        "work_date": work_date.isoformat(),
        "status": status,
        "working": working,
        "on_break": on_break,
        "open_shift_id": open_shift_id,
        "shifts": rows,
        "worked_minutes": minutes_total,
        "wage_amount": str(wage_total),
        "back_confirmed": str(back_confirmed),
        "back_pending": str(back_pending),
        "gross_amount": str(gross),
        "paid_amount": str(paid),
        "withheld_amount": str(withheld),
        "deducted_amount": str(deducted),
        "settled_amount": str(settled),
        "remaining_gross": str(remaining_gross),
        "remaining_amount": str(remaining),
        "missing_wage_setting": missing_wage,
        "items": items,
        "tax": tax,
        "payments": [
            {"id": p.id, "amount": str(p.amount), "withholding_amount": str(p.withholding_amount), "deduction_amount": str(p.deduction_amount),
             "deduction_items": p.deduction_items, "paid_at": p.paid_at.isoformat(), "cash_movement_id": p.cash_movement_id, "method": p.method}
            for p in payments
        ],
    }


def daily_payout_employees(store, work_date: date):
    """その日の対象者: この店舗に配属中の在籍者 ＋ その日にこの店舗で勤務・バック・支払いがあった人。"""
    from .models import Employee

    ids = set(
        Employee.objects.filter(company=store.company, is_active=True, assignments__store=store, assignments__is_active=True).values_list("id", flat=True)
    )
    ids |= set(Shift.objects.filter(store=store, business_date=work_date).values_list("employee_id", flat=True))
    ids |= set(BackEntry.objects.filter(store=store, business_date=work_date).values_list("employee_id", flat=True))
    ids |= set(EmployeePayment.objects.filter(store=store, work_date=work_date).values_list("employee_id", flat=True))
    return list(Employee.objects.filter(id__in=ids, company=store.company).order_by("display_name", "id"))


def close_pay_period(period, user, employees):
    now = timezone.now()
    statements = []
    for emp in employees:
        data = compute_statement(period.store, emp, period.start_date, period.end_date, period)
        st, _ = PayStatement.objects.update_or_create(
            pay_period=period, employee=emp,
            defaults={
                "worked_minutes": data["worked_minutes"],
                "wage_amount": D(data["wage_amount"]),
                "back_amount": D(data["back_amount"]),
                "gross_amount": D(data["gross_amount"]),
                "paid_amount": D(data["paid_amount"]),
                "balance_amount": D(data["balance_amount"]),
                "details": data,
            },
        )
        statements.append(st)
    period.status = "closed"
    period.closed_at = now
    period.closed_by = user
    period.save(update_fields=["status", "closed_at", "closed_by"])
    return statements
