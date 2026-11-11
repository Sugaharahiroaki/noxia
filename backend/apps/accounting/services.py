"""
経理のサービス: 日払いへの源泉徴収の組み込み、月次集計、経営者確認、納付記録、訂正。

日払い（staff.views.DailyPayoutPayView）からの流れ:
  1. payout_tax_preview(): 未渡しの報酬に対する源泉所得税・その他控除・差引支給額を出す（DB は読むだけ）
  2. settle_daily_payment(): 事業者の行をロックして計算し直し、支払う額を決める
  3. 呼び出し側がレジ出金（差引支給額）と EmployeePayment を作る
  4. record_withholding(): 源泉徴収明細と控除の使用台帳を作る
取消（現金出金の取消）では on_payment_voided() で明細を取消にし、控除を使える状態に戻す。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from apps.core.exceptions import ApiError
from apps.core.money import D, ZERO, round_yen

from . import compensation, jp_holidays
from .models import (
    DeductionUsage, MonthStatus, SalaryKind, SalaryWithholdingEntry, TaxCategory, TaxPayer, TaxRemittance, WithholdingAdjustment,
    WithholdingMonth, WithholdingRecord, WithholdingStatus,
)
from .withholding import ENGINE_VERSION, WithholdingCalc, calc_period_for, calculate_for_payment, solve_gross_for_net

CATEGORY_LABELS = dict(TaxCategory.choices)


# --- 日払いとの統合 ---------------------------------------------------------------
def _ratio(target: Decimal, gross: Decimal) -> Decimal:
    if gross <= 0:
        return Decimal(1)
    return target / gross


def _scaled_target(amount: Decimal, target_day: Decimal, gross_day: Decimal) -> Decimal:
    if gross_day <= 0 or target_day == gross_day:
        return amount
    return round_yen(amount * target_day / gross_day, "floor")


def payout_tax_preview(store, employee, work_date: date, *, items: list[dict], gross_day: Decimal, remaining_gross: Decimal,
                       deducted: dict[str, Decimal], selected: list[str] | None = None, paid_on: date | None = None) -> dict:
    """
    未渡しの報酬（remaining_gross）を今渡すときの源泉所得税・その他控除・差引支給額。
    deducted: 同じ勤務日の支払いで差し引き済みの控除（控除ごと）。selected: チェックした店舗の控除項目（None なら既定）。
    """
    paid_on = paid_on or timezone.localdate()
    enabled = compensation.withholding_enabled(store)
    profile = compensation.tax_profile(employee, store, work_date)
    contract = profile["contract"]
    target_day = compensation.target_of(items)
    ded_rows, options = ([], [])
    if remaining_gross > 0:
        ded_rows, options = compensation.payout_deductions(store, contract, gross_day, deducted=deducted, selected=selected)
    ded_remaining = sum((D(r["amount"]) for r in ded_rows), ZERO)
    optional = sum((D(r["amount"]) for r in ded_rows if r.get("optional")), ZERO)
    remaining_target = _scaled_target(remaining_gross, target_day, gross_day) if remaining_gross > 0 else ZERO
    result = {
        "withholding_enabled": enabled,
        "category": profile["category"],
        "category_label": profile["label"],
        "confirmed": profile["confirmed"],
        "contract_id": contract.id if contract else None,
        "status": "none",
        "reasons": list(profile["reasons"]) if enabled else [],
        "period_start": None,
        "period_end": None,
        "target_amount": str(remaining_target),
        "deduction_available": "0",
        "deduction_applied": "0",
        "withholding_amount": "0",
        "rate_percent": "0",
        "rule_version": "",
        "explanation": "",
        "other_deductions": ded_rows,
        "other_deduction_amount": str(ded_remaining),
        "deduction_options": options,
        "net_amount": str(remaining_gross - ded_remaining),
        # 店舗の控除項目をどれもチェックしないときの額（画面でチェックを付け外ししたときの計算に使う）
        "net_without_options": str(remaining_gross - ded_remaining + optional),
    }
    if remaining_gross <= 0:
        result["status"] = "settled"
        result["net_amount"] = result["net_without_options"] = str(remaining_gross)
        return result
    if not enabled:
        result["status"] = "disabled"
        result["explanation"] = "この店舗は源泉所得税を差し引かない設定です（店舗設定）。"
        return result
    calc = None
    if profile["category"] == TaxCategory.HOSTESS_FEE:
        period = calc_period_for(profile["period_type"], work_date)
        calc = calculate_for_payment(compensation.taxpayer_for(store), employee, target_amount=remaining_target, period=period, paid_on=paid_on)
        result["status"] = calc.status
        result["reasons"] += calc.reasons
    elif profile["category"] == TaxCategory.SALARY:
        result["status"] = "external"
        result["explanation"] = "給与所得の源泉徴収は、給与計算ソフト（税額表の甲欄・乙欄・丙欄、扶養・社会保険の情報）で計算してください。NOXIA では計算しません。"
    else:
        result["status"] = "needs_review"
    if calc is not None:
        if calc.period_start:
            result["period_start"] = calc.period_start.isoformat()
            result["period_end"] = calc.period_end.isoformat()
        if calc.status == "calculated":
            result.update({
                "deduction_available": str(calc.deduction_available),
                "deduction_applied": str(calc.deduction_applied),
                "withholding_amount": str(calc.withholding_amount),
                "rate_percent": str(calc.rate_percent),
                "rule_version": calc.rule_version,
                "explanation": calc.basis()["explanation"],
                "net_amount": str(remaining_gross - calc.withholding_amount - ded_remaining),
                "net_without_options": str(remaining_gross - calc.withholding_amount - ded_remaining + optional),
            })
    return result


@dataclass
class Settlement:
    gross: Decimal
    target: Decimal
    withholding: Decimal
    other_deduction: Decimal
    net: Decimal
    category: str
    status: str
    reasons: list[str] = field(default_factory=list)
    calc: WithholdingCalc | None = None
    items: list[dict] = field(default_factory=list)
    deduction_rows: list[dict] = field(default_factory=list)
    contract_id: int | None = None
    period: tuple[date, date] | None = None
    taxpayer: TaxPayer | None = None


def settle_daily_payment(store, employee, work_date: date, info: dict, *, cash_amount: Decimal | None, paid_on: date,
                         selected: list[str] | None = None) -> Settlement:
    """
    事業者の行をロックしてから計算し直す（同じキャストへの同時の支払いで控除を二重に使わない）。
    cash_amount: 「金額を変えて渡す」の手渡し額。None なら未渡しの全額を精算する。
    selected: チェックした店舗の控除項目（None なら既定）。
    """
    enabled = compensation.withholding_enabled(store)
    taxpayer = compensation.taxpayer_for(store)
    if taxpayer is not None and enabled:
        taxpayer = TaxPayer.objects.select_for_update().get(pk=taxpayer.pk)
    items = info["items"]
    gross_day = D(info["gross_amount"])
    remaining_gross = D(info["remaining_gross"])
    pv = payout_tax_preview(store, employee, work_date, items=items, gross_day=gross_day, remaining_gross=remaining_gross,
                            deducted=compensation.deducted_by_code(info["payments"]), selected=selected, paid_on=paid_on)
    profile = compensation.tax_profile(employee, store, work_date)
    target_day = compensation.target_of(items)
    ded_remaining = D(pv["other_deduction_amount"])
    hostess = enabled and profile["category"] == TaxCategory.HOSTESS_FEE
    period = calc_period_for(profile["period_type"], work_date) if hostess else None
    gross = remaining_gross
    if cash_amount is not None and D(cash_amount) != D(pv["net_amount"]):
        cash = D(cash_amount)
        if pv["status"] == "calculated":
            g = solve_gross_for_net(cash + ded_remaining, D(pv["deduction_available"]), D(pv["rate_percent"]),
                                    target_ratio=_ratio(target_day, gross_day))
        else:
            g = cash + ded_remaining
        if g > remaining_gross:
            raise ApiError("validation_error", f"未渡しの額（¥{D(pv['net_amount']):,.0f}）より多くは渡せません。")
        gross = g
    target = _scaled_target(gross, target_day, gross_day)
    calc = None
    withholding = ZERO
    status = pv["status"]
    reasons = list(pv["reasons"])
    if hostess:
        calc = calculate_for_payment(taxpayer, employee, target_amount=target, period=period, paid_on=paid_on)
        status = calc.status
        reasons = list(profile["reasons"]) + calc.reasons
        if calc.status == "calculated":
            withholding = calc.withholding_amount
    net = gross - withholding - ded_remaining
    if net <= 0:
        raise ApiError("validation_error", "差引支給額が0円以下になるため渡せません。控除の設定を確認してください。")
    return Settlement(
        gross=gross, target=target, withholding=withholding, other_deduction=ded_remaining, net=net, category=profile["category"],
        status=status, reasons=reasons, calc=calc, items=items, deduction_rows=pv["other_deductions"],
        contract_id=pv["contract_id"], period=period, taxpayer=taxpayer,
    )


def settle_simple(store, employee, *, amount: Decimal, on_date: date, paid_on: date, mode: str = "net",
                  period: tuple[date, date] | None = None, manual_withholding: Decimal | None = None) -> Settlement:
    """
    日払い画面以外の支払い（振込の締め支払い、レジの手入力出金など）。
      mode="gross": amount は報酬額。ホステス報酬（確認済み）は源泉所得税を計算して差し引く。
      mode="net":   amount は渡した額。源泉徴収は計算せず、給与以外は「要確認」として月次に出す。
    給与（確認済み）は manual_withholding（給与計算ソフトで計算した額）をそのまま記録する。
    店舗設定で源泉所得税を差し引かない場合は、amount をそのまま渡した額として記録する（源泉徴収明細は作らない）。
    """
    profile = compensation.tax_profile(employee, store, on_date)
    taxpayer = compensation.taxpayer_for(store)
    amount = D(amount)
    items = [{"code": "lump_sum", "label": "精算額（一括）", "amount": str(amount), "withholding_target": True}]
    category = profile["category"]
    contract_id = profile["contract"].id if profile["contract"] else None
    if not compensation.withholding_enabled(store):
        if manual_withholding:
            raise ApiError("validation_error", "この店舗は源泉所得税を差し引かない設定です（店舗設定）。源泉徴収額は入力できません。")
        return Settlement(gross=amount, target=amount, withholding=ZERO, other_deduction=ZERO, net=amount, category=category, status="disabled",
                          items=items, contract_id=contract_id, taxpayer=taxpayer)
    if taxpayer is not None:
        taxpayer = TaxPayer.objects.select_for_update().get(pk=taxpayer.pk)
    reasons = list(profile["reasons"])
    if category == TaxCategory.SALARY:
        w = D(manual_withholding or 0)
        gross = amount + w if mode == "net" else amount
        return Settlement(gross=gross, target=gross, withholding=w, other_deduction=ZERO, net=gross - w, category=category, status="external",
                          items=items, contract_id=profile["contract"].id if profile["contract"] else None, taxpayer=taxpayer)
    if category == TaxCategory.HOSTESS_FEE and mode == "gross":
        period = period or calc_period_for(profile["period_type"], on_date)
        calc = calculate_for_payment(taxpayer, employee, target_amount=amount, period=period, paid_on=paid_on)
        w = calc.withholding_amount if calc.status == "calculated" else ZERO
        if amount - w <= 0:
            raise ApiError("validation_error", "差引支給額が0円以下になります。")
        return Settlement(gross=amount, target=amount, withholding=w, other_deduction=ZERO, net=amount - w, category=category, status=calc.status,
                          reasons=reasons + calc.reasons, calc=calc, items=items, contract_id=profile["contract"].id, period=period, taxpayer=taxpayer)
    if category == TaxCategory.HOSTESS_FEE:
        reasons.append("渡した額だけが記録された支払いのため、源泉所得税を計算していません（報酬額を確認して確定してください）。")
    return Settlement(gross=amount, target=amount, withholding=ZERO, other_deduction=ZERO, net=amount, category=category, status="needs_review",
                      reasons=reasons, items=items, contract_id=profile["contract"].id if profile["contract"] else None, period=period,
                      taxpayer=taxpayer)


def record_salary_entry_for_payment(payment, settlement: Settlement, user) -> SalaryWithholdingEntry | None:
    """給与（確認済み）の支払いで源泉徴収額を入力した場合、給与の徴収高計算書の集計にも入れる。"""
    if settlement.category != TaxCategory.SALARY or settlement.withholding <= 0 or settlement.taxpayer is None:
        return None
    return SalaryWithholdingEntry.objects.create(
        company=payment.store.company, taxpayer=settlement.taxpayer, store=payment.store, employee=payment.employee, kind=SalaryKind.REGULAR,
        paid_on=timezone.localdate(payment.paid_at), gross_amount=settlement.gross, withholding_amount=settlement.withholding,
        source="noxia_payment", payment=payment, note="支払い登録時に入力（給与計算ソフトの計算結果）", created_by=user,
    )


def _record_status(settlement: Settlement) -> str:
    if settlement.category == TaxCategory.SALARY:
        return WithholdingStatus.EXTERNAL
    if settlement.category == TaxCategory.HOSTESS_FEE and settlement.status == "calculated":
        return WithholdingStatus.CALCULATED
    return WithholdingStatus.NEEDS_REVIEW


def record_withholding(payment, settlement: Settlement, *, work_date: date | None, user) -> WithholdingRecord | None:
    """源泉徴収明細を作る。店舗設定で源泉所得税を差し引かない支払いは作らない（None）。"""
    if settlement.status == "disabled":
        return None
    calc = settlement.calc
    paid_on = timezone.localdate(payment.paid_at)
    rec = WithholdingRecord.objects.create(
        company=payment.store.company, taxpayer=settlement.taxpayer, store=payment.store, employee=payment.employee, payment=payment,
        contract_id=settlement.contract_id, paid_at=payment.paid_at, paid_on=paid_on, work_date=work_date,
        tax_category=settlement.category, status=_record_status(settlement), review_reasons=settlement.reasons,
        period_start=calc.period_start if calc else (settlement.period[0] if settlement.period else None),
        period_end=calc.period_end if calc else (settlement.period[1] if settlement.period else None),
        period_days=calc.period_days if calc else 0,
        gross_amount=settlement.gross, target_amount=settlement.target,
        deduction_available=calc.deduction_available if calc else ZERO,
        deduction_applied=calc.deduction_applied if calc and calc.status == "calculated" else ZERO,
        taxable_amount=calc.taxable_amount if calc else ZERO,
        rule_id=calc.rule_id if calc else None, rule_version=calc.rule_version if calc else "",
        rate_percent=calc.rate_percent if calc else ZERO,
        withholding_amount=settlement.withholding, other_deduction_amount=settlement.other_deduction, net_amount=settlement.net,
        items=settlement.items, other_deductions=settlement.deduction_rows,
        calc_basis=calc.basis() if calc else {"engine_version": ENGINE_VERSION, "status": settlement.status, "reasons": settlement.reasons},
        engine_version=ENGINE_VERSION, created_by=user,
    )
    if settlement.taxpayer is not None:
        if calc is not None and calc.status == "calculated":
            DeductionUsage.objects.bulk_create([
                DeductionUsage(record=rec, taxpayer=settlement.taxpayer, employee=payment.employee, date=d, amount=a) for d, a in calc.allocations
            ])
        elif settlement.category == TaxCategory.SALARY and work_date is not None:
            # 同じ日に給与等を払っていれば、その日のホステス報酬の控除額から差し引く（施行令322条）
            _consume_for_salary(rec, None, settlement.taxpayer, payment.employee, work_date, work_date, settlement.gross)
    return rec


def _consume_for_salary(record, salary_entry, taxpayer, employee, start: date, end: date, amount: Decimal):
    from .withholding import period_dates, rule_for, used_deductions

    rule = rule_for("hostess_fee", end)
    if rule is None or employee is None:
        return
    used = used_deductions(taxpayer, employee, start, end)
    need = D(amount)
    rows = []
    for d in period_dates(start, end):
        if need <= 0:
            break
        rest = D(rule.daily_deduction) - D(used.get(d, ZERO))
        if rest <= 0:
            continue
        take = min(rest, need)
        rows.append(DeductionUsage(record=record, salary_entry=salary_entry, taxpayer=taxpayer, employee=employee, date=d, amount=take))
        need -= take
    DeductionUsage.objects.bulk_create(rows)


def on_payment_voided(payment, reason: str, user) -> None:
    rec = WithholdingRecord.objects.select_for_update().filter(payment=payment).first()
    if rec is None or rec.is_voided:
        return
    rec.is_voided = True
    rec.status = WithholdingStatus.VOIDED
    rec.void_reason = (reason or "")[:200]
    rec.voided_at = timezone.now()
    rec.save(update_fields=["is_voided", "status", "void_reason", "voided_at", "updated_at"])
    DeductionUsage.objects.filter(record=rec).delete()
    for e in SalaryWithholdingEntry.objects.filter(payment=payment, is_voided=False):
        e.is_voided = True
        e.void_reason = (reason or "")[:200]
        e.save(update_fields=["is_voided", "void_reason", "updated_at"])
        DeductionUsage.objects.filter(salary_entry=e).delete()


@transaction.atomic
def resolve_needs_review(record: WithholdingRecord, user, *, category: str, period: tuple[date, date] | None, note: str) -> WithholdingRecord:
    """
    支払時に税務区分などが不明だった明細を、経営者が確認して確定する。
    支払済みの手渡し額は変えない。ホステス報酬と確定した場合は、未徴収の税額を「訂正（支払時に未徴収）」として支払月に計上する。
    """
    record = WithholdingRecord.objects.select_for_update().get(pk=record.pk)
    if record.status != WithholdingStatus.NEEDS_REVIEW or record.is_voided:
        raise ApiError("not_needs_review", "要確認の明細ではありません。", status=409)
    if not (note or "").strip():
        raise ApiError("validation_error", "確認した内容（メモ）を入力してください。")
    basis = dict(record.calc_basis or {})
    basis["resolution"] = {"by": user.id, "at": timezone.now().isoformat(), "category": category, "note": note}
    if category == TaxCategory.SALARY:
        record.tax_category = TaxCategory.SALARY
        record.status = WithholdingStatus.EXTERNAL
    elif category == TaxCategory.HOSTESS_FEE:
        if record.taxpayer is None:
            raise ApiError("validation_error", "店舗の事業者を設定してから確定してください。")
        if period is None:
            raise ApiError("validation_error", "報酬計算期間（開始日・終了日）を入力してください。")
        taxpayer = TaxPayer.objects.select_for_update().get(pk=record.taxpayer_id)
        calc = calculate_for_payment(taxpayer, record.employee, target_amount=record.target_amount, period=period, paid_on=record.paid_on)
        if calc.status != "calculated":
            raise ApiError("validation_error", "計算できません: " + " ".join(calc.reasons))
        DeductionUsage.objects.bulk_create([
            DeductionUsage(record=record, taxpayer=taxpayer, employee=record.employee, date=d, amount=a) for d, a in calc.allocations
        ])
        record.tax_category = TaxCategory.HOSTESS_FEE
        record.status = WithholdingStatus.CALCULATED
        record.period_start, record.period_end, record.period_days = calc.period_start, calc.period_end, calc.period_days
        record.deduction_available = calc.deduction_available
        record.deduction_applied = calc.deduction_applied
        record.taxable_amount = calc.taxable_amount
        record.rule_id, record.rule_version, record.rate_percent = calc.rule_id, calc.rule_version, calc.rate_percent
        basis["resolution"]["calc"] = calc.basis()
        if calc.withholding_amount > 0:
            WithholdingAdjustment.objects.create(
                company=record.company, taxpayer=taxpayer, employee=record.employee, record=record, category=TaxCategory.HOSTESS_FEE,
                kind="unwithheld", applies_on=record.paid_on, target_delta=ZERO, deduction_delta=ZERO, amount_delta=calc.withholding_amount,
                reason=f"支払時に未徴収（税務区分の確認後に計算）: {note}"[:300], created_by=user,
            )
    else:
        raise ApiError("validation_error", "税務区分を選んでください。")
    record.calc_basis = basis
    record.save()
    return record


# --- 月次集計 ------------------------------------------------------------------
def month_period(taxpayer: TaxPayer, category: str, any_day: date) -> tuple[date, date, date]:
    """(期間開始, 期間終了, 納期限)。ホステス報酬は毎月。給与は納期の特例があれば半年。"""
    if category == TaxCategory.SALARY and taxpayer.payroll_due_special:
        return jp_holidays.special_due_period(any_day)
    start = any_day.replace(day=1)
    return start, jp_holidays.month_end(start), jp_holidays.monthly_due_date(start)


def hostess_totals(taxpayer: TaxPayer, start: date, end: date) -> dict:
    recs = WithholdingRecord.objects.filter(taxpayer=taxpayer, paid_on__gte=start, paid_on__lte=end, is_voided=False).exclude(
        status=WithholdingStatus.EXTERNAL
    ).select_related("employee", "store")
    calculated = [r for r in recs if r.status == WithholdingStatus.CALCULATED and r.tax_category == TaxCategory.HOSTESS_FEE]
    review = [r for r in recs if r.status == WithholdingStatus.NEEDS_REVIEW]
    adjustments = list(WithholdingAdjustment.objects.filter(
        taxpayer=taxpayer, category=TaxCategory.HOSTESS_FEE, applies_on__gte=start, applies_on__lte=end, is_voided=False
    ))
    adj_amount = sum((a.amount_delta for a in adjustments), ZERO)
    adj_target = sum((a.target_delta for a in adjustments), ZERO)
    adj_ded = sum((a.deduction_delta for a in adjustments), ZERO)
    by_store: dict[int, dict] = {}
    by_emp: dict[int, dict] = {}
    for r in calculated:
        s = by_store.setdefault(r.store_id, {"store_id": r.store_id, "store_name": r.store.name, "people": set(), "count": 0, "gross": ZERO, "target": ZERO, "deduction": ZERO, "withholding": ZERO})
        e = by_emp.setdefault(r.employee_id, {"employee_id": r.employee_id, "employee_name": r.employee.display_name, "full_name": r.employee.full_name, "count": 0, "gross": ZERO, "target": ZERO, "deduction": ZERO, "withholding": ZERO, "stores": set()})
        for row in (s, e):
            row["count"] += 1
            row["gross"] += r.gross_amount
            row["target"] += r.target_amount
            row["deduction"] += r.deduction_applied
            row["withholding"] += r.withholding_amount
        s["people"].add(r.employee_id)
        e["stores"].add(r.store.name)
    for a in adjustments:
        if a.employee_id and a.employee_id in by_emp:
            by_emp[a.employee_id]["withholding"] += a.amount_delta
    withheld = sum((r.withholding_amount for r in calculated), ZERO)
    totals = {
        "gross_total": sum((r.gross_amount for r in recs), ZERO),           # 報酬支払総額（要確認を含む）
        "target_total": sum((r.target_amount for r in calculated), ZERO) + adj_target,  # 源泉徴収対象額（法定の「支払額」）
        "deduction_total": sum((r.deduction_applied for r in calculated), ZERO) + adj_ded,  # 摘要「支払額からの控除額」
        "withheld_total": withheld,                                          # 支払時に源泉徴収した額
        "adjustment_total": adj_amount,
        "tax_total": withheld + adj_amount,                                  # 納付する税額
        "people": len({r.employee_id for r in calculated}),
        "payment_count": len(calculated),
        "needs_review_count": len(review),
        "needs_review_amount": sum((r.gross_amount for r in review), ZERO),
    }
    return {
        "totals": totals,
        "by_store": [{**v, "people": len(v["people"])} for v in by_store.values()],
        "by_employee": sorted([{**v, "stores": sorted(v["stores"])} for v in by_emp.values()], key=lambda x: x["employee_name"]),
        "needs_review": review,
        "adjustments": adjustments,
    }


def salary_totals(taxpayer: TaxPayer, start: date, end: date) -> dict:
    entries = list(SalaryWithholdingEntry.objects.filter(taxpayer=taxpayer, paid_on__gte=start, paid_on__lte=end, is_voided=False).select_related("employee"))
    rows = {}
    for kind, label in SalaryKind.choices:
        ks = [e for e in entries if e.kind == kind]
        people = {e.employee_id for e in ks if e.employee_id}
        anonymous = sum((e.headcount for e in ks if not e.employee_id), 0)
        rows[kind] = {
            "kind": kind, "label": label,
            # 日雇労務者の賃金は延べ人員、それ以外は実人員
            "people": (sum((1 for e in ks if e.employee_id), 0) + anonymous) if kind == SalaryKind.DAILY_WORKER else len(people) + anonymous,
            "gross": sum((e.gross_amount for e in ks), ZERO),
            "withholding": sum((e.withholding_amount for e in ks), ZERO),
            "count": len(ks),
        }
    adjustments = list(WithholdingAdjustment.objects.filter(
        taxpayer=taxpayer, category=TaxCategory.SALARY, applies_on__gte=start, applies_on__lte=end, is_voided=False
    ))
    adj = sum((a.amount_delta for a in adjustments), ZERO)
    noxia = WithholdingRecord.objects.filter(taxpayer=taxpayer, status=WithholdingStatus.EXTERNAL, paid_on__gte=start, paid_on__lte=end, is_voided=False)
    withheld = sum((r["withholding"] for r in rows.values()), ZERO)
    return {
        "totals": {
            "gross_total": sum((r["gross"] for r in rows.values()), ZERO),
            "withheld_total": withheld,
            "adjustment_total": adj,
            "tax_total": withheld + adj,
            "people": rows[SalaryKind.REGULAR]["people"],
            "payment_count": len(entries),
            "needs_review_count": 0,
            "noxia_salary_paid": noxia.aggregate(s=Sum("gross_amount"))["s"] or ZERO,
            "noxia_salary_count": noxia.count(),
        },
        "rows": list(rows.values()),
        "entries": entries,
        "adjustments": adjustments,
    }


def _totals(taxpayer, category, start, end) -> dict:
    return hostess_totals(taxpayer, start, end) if category == TaxCategory.HOSTESS_FEE else salary_totals(taxpayer, start, end)


def _snapshot(totals: dict) -> tuple[dict, str]:
    snap = {k: str(v) for k, v in sorted(totals.items())}
    h = hashlib.sha256(json.dumps(snap, sort_keys=True).encode()).hexdigest()
    return snap, h


def remitted_total(month: WithholdingMonth) -> Decimal:
    return month.remittances.filter(is_voided=False).aggregate(s=Sum("amount"))["s"] or ZERO


def display_status(month: WithholdingMonth | None, totals: dict, *, today: date | None = None, due: date | None = None) -> str:
    today = today or timezone.localdate()
    if month is None:
        if due and today > due and (totals["tax_total"] > 0 or totals["needs_review_count"]):
            return MonthStatus.OVERDUE
        return MonthStatus.NOT_AGGREGATED
    _, current_hash = _snapshot(totals)
    changed = current_hash != month.snapshot_hash
    confirmed = month.status in (MonthStatus.OWNER_CONFIRMED, MonthStatus.AWAITING_PAYMENT, MonthStatus.PAID)
    tax_total = D(totals["tax_total"])
    paid = remitted_total(month)
    if confirmed and changed:
        return MonthStatus.CORRECTED  # 確認後に支払い・取消・訂正があった
    if totals["needs_review_count"]:
        return MonthStatus.NEEDS_REVIEW
    if confirmed and paid > 0 and paid >= tax_total:
        return MonthStatus.PAID
    if today > month.due_date and paid < tax_total:
        return MonthStatus.OVERDUE
    if changed:
        return MonthStatus.NOT_AGGREGATED  # 集計後に変わったので集計し直す
    return month.status


def month_view(taxpayer: TaxPayer, category: str, any_day: date, *, detail: bool = True) -> dict:
    start, end, due = month_period(taxpayer, category, any_day)
    data = _totals(taxpayer, category, start, end)
    month = WithholdingMonth.objects.filter(taxpayer=taxpayer, category=category, period_start=start).first()
    status = display_status(month, data["totals"], due=due)
    paid = remitted_total(month) if month else ZERO
    snap_diff = {}
    if month and month.snapshot:
        cur, _ = _snapshot(data["totals"])
        snap_diff = {k: {"before": month.snapshot.get(k), "after": v} for k, v in cur.items() if month.snapshot.get(k) != v}
    today = timezone.localdate()
    out = {
        "taxpayer_id": taxpayer.id,
        "taxpayer_name": taxpayer.name,
        "category": category,
        "category_label": CATEGORY_LABELS.get(category, category),
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
        "label": jp_holidays.reiwa_label(start.year, start.month) + ("" if start.month == end.month else f"〜{end.month}月"),
        "due_date": due.isoformat(),
        "days_to_due": (due - today).days,
        "special_due": category == TaxCategory.SALARY and taxpayer.payroll_due_special,
        "status": status,
        "status_label": dict(MonthStatus.choices)[status],
        "month_id": month.id if month else None,
        "confirmed_at": month.confirmed_at.isoformat() if month and month.confirmed_at else None,
        "confirmed_by": str(month.confirmed_by) if month and month.confirmed_by else None,
        "advisor_note": month.advisor_note if month else "",
        "advisor_checked_at": month.advisor_checked_at.isoformat() if month and month.advisor_checked_at else None,
        "totals": {k: (str(v) if isinstance(v, Decimal) else v) for k, v in data["totals"].items()},
        "remitted_total": str(paid),
        "unpaid_total": str(max(D(data["totals"]["tax_total"]) - paid, ZERO)),
        "snapshot_diff": snap_diff,
    }
    if detail:
        if category == TaxCategory.HOSTESS_FEE:
            out["by_store"] = [{k: (str(v) if isinstance(v, Decimal) else v) for k, v in r.items()} for r in data["by_store"]]
            out["by_employee"] = [{k: (str(v) if isinstance(v, Decimal) else v) for k, v in r.items()} for r in data["by_employee"]]
            out["needs_review"] = [record_brief(r) for r in data["needs_review"]]
        else:
            out["rows"] = [{k: (str(v) if isinstance(v, Decimal) else v) for k, v in r.items()} for r in data["rows"]]
            out["entries"] = [salary_entry_brief(e) for e in data["entries"]]
        out["adjustments"] = [adjustment_brief(a) for a in data["adjustments"]]
        out["remittances"] = [remittance_brief(r) for r in month.remittances.all()] if month else []
    return out


def record_brief(r: WithholdingRecord) -> dict:
    return {
        "id": r.id, "store_id": r.store_id, "store_name": r.store.name, "employee_id": r.employee_id, "employee_name": r.employee.display_name,
        "paid_on": r.paid_on.isoformat(), "work_date": r.work_date.isoformat() if r.work_date else None,
        "tax_category": r.tax_category, "status": r.status, "status_label": r.get_status_display(), "review_reasons": r.review_reasons,
        "period_start": r.period_start.isoformat() if r.period_start else None, "period_end": r.period_end.isoformat() if r.period_end else None,
        "period_days": r.period_days, "gross_amount": str(r.gross_amount), "target_amount": str(r.target_amount),
        "deduction_applied": str(r.deduction_applied), "withholding_amount": str(r.withholding_amount),
        "other_deduction_amount": str(r.other_deduction_amount), "net_amount": str(r.net_amount), "rate_percent": str(r.rate_percent),
        "rule_version": r.rule_version, "is_voided": r.is_voided, "void_reason": r.void_reason, "items": r.items,
        "other_deductions": r.other_deductions, "calc_basis": r.calc_basis,
    }


def salary_entry_brief(e: SalaryWithholdingEntry) -> dict:
    return {
        "id": e.id, "kind": e.kind, "kind_label": e.get_kind_display(), "paid_on": e.paid_on.isoformat(),
        "employee_id": e.employee_id, "employee_name": e.employee.display_name if e.employee else None, "headcount": e.headcount,
        "period_start": e.period_start.isoformat() if e.period_start else None, "period_end": e.period_end.isoformat() if e.period_end else None,
        "gross_amount": str(e.gross_amount), "withholding_amount": str(e.withholding_amount), "note": e.note, "is_voided": e.is_voided,
    }


def adjustment_brief(a: WithholdingAdjustment) -> dict:
    return {
        "id": a.id, "kind": a.kind, "kind_label": a.get_kind_display(), "applies_on": a.applies_on.isoformat(), "employee_id": a.employee_id,
        "record_id": a.record_id, "target_delta": str(a.target_delta), "deduction_delta": str(a.deduction_delta),
        "amount_delta": str(a.amount_delta), "reason": a.reason, "is_voided": a.is_voided, "created_at": a.created_at.isoformat(),
    }


def remittance_brief(r: TaxRemittance) -> dict:
    return {
        "id": r.id, "paid_on": r.paid_on.isoformat(), "amount": str(r.amount), "method": r.method, "method_label": r.get_method_display(),
        "paid_from": r.paid_from, "evidence_id": r.evidence_id, "note": r.note, "is_voided": r.is_voided, "void_reason": r.void_reason,
    }


@transaction.atomic
def aggregate_month(taxpayer: TaxPayer, category: str, any_day: date, user) -> WithholdingMonth:
    """集計する（集計時点の数字を保存）。確認済みの月を集計し直すと、確認からやり直す。"""
    start, end, due = month_period(taxpayer, category, any_day)
    data = _totals(taxpayer, category, start, end)
    snap, h = _snapshot(data["totals"])
    month, _ = WithholdingMonth.objects.select_for_update().get_or_create(
        taxpayer=taxpayer, category=category, period_start=start,
        defaults={"company": taxpayer.company, "period_end": end, "due_date": due},
    )
    status = MonthStatus.NEEDS_REVIEW if data["totals"]["needs_review_count"] else MonthStatus.AGGREGATED
    if month.status == MonthStatus.PAID and h == month.snapshot_hash:
        status = MonthStatus.PAID
    month.period_end = end
    month.due_date = due
    month.snapshot = snap
    month.snapshot_hash = h
    month.status = status
    month.aggregated_at = timezone.now()
    month.aggregated_by = user
    if status != MonthStatus.PAID:
        month.confirmed_at = None
        month.confirmed_by = None
    month.save()
    return month


@transaction.atomic
def confirm_month(month: WithholdingMonth, user) -> WithholdingMonth:
    """経営者が数字を確認した（帳票作成・納付の前提）。"""
    month = WithholdingMonth.objects.select_for_update().get(pk=month.pk)
    data = _totals(month.taxpayer, month.category, month.period_start, month.period_end)
    _, h = _snapshot(data["totals"])
    if data["totals"]["needs_review_count"]:
        raise ApiError("needs_review", f"要確認の支払いが {data['totals']['needs_review_count']} 件あります。先に確認してください。", status=409)
    if h != month.snapshot_hash:
        raise ApiError("stale", "集計後に支払い・訂正がありました。もう一度「集計する」を押して数字を確認してください。", status=409)
    month.status = MonthStatus.OWNER_CONFIRMED
    month.confirmed_at = timezone.now()
    month.confirmed_by = user
    month.save(update_fields=["status", "confirmed_at", "confirmed_by", "updated_at"])
    return month


@transaction.atomic
def mark_awaiting_payment(month: WithholdingMonth) -> WithholdingMonth:
    month = WithholdingMonth.objects.select_for_update().get(pk=month.pk)
    if month.status not in (MonthStatus.OWNER_CONFIRMED, MonthStatus.AWAITING_PAYMENT):
        raise ApiError("not_confirmed", "経営者確認のあとで納付待ちにできます。", status=409)
    month.status = MonthStatus.AWAITING_PAYMENT
    month.save(update_fields=["status", "updated_at"])
    return month


@transaction.atomic
def record_remittance(month: WithholdingMonth, user, *, paid_on: date, amount: Decimal, method: str, paid_from: str, note: str = "",
                      evidence=None, store=None) -> TaxRemittance:
    month = WithholdingMonth.objects.select_for_update().get(pk=month.pk)
    if month.status not in (MonthStatus.OWNER_CONFIRMED, MonthStatus.AWAITING_PAYMENT, MonthStatus.PAID):
        raise ApiError("not_confirmed", "経営者確認が済んだ月だけ納付を記録できます。", status=409)
    if amount <= 0:
        raise ApiError("validation_error", "納付額は1円以上で入力してください。")
    cash_movement = None
    if paid_from == "cash_register":
        from apps.cash.models import CashMovement, CashMovementKind
        from apps.cash.services import require_open_business_day

        if store is None:
            raise ApiError("validation_error", "レジ現金から納付した店舗を選んでください。")
        from .compensation import taxpayer_for

        if taxpayer_for(store) != month.taxpayer:
            raise ApiError("validation_error", "この事業者の店舗のレジを選んでください。")
        day = require_open_business_day(store)
        cash_movement = CashMovement.objects.create(
            store=store, business_day=day, kind=CashMovementKind.OTHER_OUT, amount=amount, occurred_at=timezone.now(), performed_by=user,
            reason=f"源泉所得税の納付（{month.period_start:%Y-%m}分）"[:200],
        )
    rem = TaxRemittance.objects.create(
        company=month.company, taxpayer=month.taxpayer, month=month, paid_on=paid_on, amount=amount, method=method, paid_from=paid_from,
        cash_movement=cash_movement, evidence=evidence, note=(note or "")[:200], recorded_by=user,
    )
    data = _totals(month.taxpayer, month.category, month.period_start, month.period_end)
    if remitted_total(month) >= D(data["totals"]["tax_total"]):
        month.status = MonthStatus.PAID
        month.save(update_fields=["status", "updated_at"])
    return rem


def remittance_check(month: WithholdingMonth) -> dict:
    """納付額の照合: 納付すべき税額と納付記録の合計。"""
    data = _totals(month.taxpayer, month.category, month.period_start, month.period_end)
    tax_total = D(data["totals"]["tax_total"])
    paid = remitted_total(month)
    return {"tax_total": str(tax_total), "remitted_total": str(paid), "difference": str(paid - tax_total), "matched": paid == tax_total}


def alerts(taxpayer: TaxPayer, *, today: date | None = None, months_back: int = 6) -> list[dict]:
    """未納付・期限が近い月のアラート。"""
    today = today or timezone.localdate()
    out = []
    seen = set()
    first = today.replace(day=1)
    for i in range(0, months_back + 1):
        d = jp_holidays.add_month(first, -i)
        for category in (TaxCategory.HOSTESS_FEE, TaxCategory.SALARY):
            start, _, due = month_period(taxpayer, category, d)
            if (category, start) in seen:
                continue  # 納期の特例の半年分は1回だけ
            seen.add((category, start))
            v = month_view(taxpayer, category, d, detail=False)
            tax_total = D(v["totals"]["tax_total"])
            if v["status"] == MonthStatus.PAID or (tax_total == 0 and not v["totals"]["needs_review_count"]):
                continue
            level = "overdue" if today > due else ("soon" if (due - today).days <= 5 else "info")
            if level == "info" and v["status"] not in (MonthStatus.NEEDS_REVIEW, MonthStatus.CORRECTED):
                continue
            out.append({"level": level, "category": category, "label": v["label"], "category_label": v["category_label"], "due_date": v["due_date"],
                        "status": v["status"], "status_label": v["status_label"], "unpaid_total": v["unpaid_total"], "period_start": v["period_start"]})
    return out


def store_summary(store, start: date, end: date) -> dict:
    """店舗単位の管理集計（法定集計は事業者単位の month_view）。"""
    recs = WithholdingRecord.objects.filter(store=store, paid_on__gte=start, paid_on__lte=end, is_voided=False)
    calc = recs.filter(status=WithholdingStatus.CALCULATED)
    agg = calc.aggregate(g=Sum("gross_amount"), t=Sum("target_amount"), w=Sum("withholding_amount"), d=Sum("deduction_applied"))
    return {
        "store_id": store.id, "store_name": store.name, "from": start.isoformat(), "to": end.isoformat(),
        "gross_total": str(recs.aggregate(s=Sum("gross_amount"))["s"] or ZERO),
        "target_total": str(agg["t"] or ZERO), "withheld_total": str(agg["w"] or ZERO), "deduction_total": str(agg["d"] or ZERO),
        "people": calc.values("employee_id").distinct().count(), "payment_count": calc.count(),
        "needs_review_count": recs.filter(status=WithholdingStatus.NEEDS_REVIEW).count(),
        "salary_paid_total": str(recs.filter(status=WithholdingStatus.EXTERNAL).aggregate(s=Sum("gross_amount"))["s"] or ZERO),
    }
