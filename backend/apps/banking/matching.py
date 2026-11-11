"""
照合候補の計算。候補は提案までで、確定は人が行う（services.confirm）。

ルール:
- card_settlement: 店舗×期間（1日／週／半月／月）のカード売上の合計と入金額が一致、または差が手数料の範囲（既定 5% か 1,100 円の大きい方）
- bank_deposit: cash.BankDeposit（売上金の預入）と金額一致・日付 ±3 日
- employee_payment: 振込の支払い（method=bank）と金額一致・±7 日
- expense: paid_from=bank の経費と金額一致・±7 日（取引先の類似度で加点）
- payable_payment: expenses.PayablePayment（method=bank）と金額一致・±7 日（アプリがあれば）
- owner_transfer / tax_remittance: 金額一致・±7 日
- transfer: 同じ事業者の別口座に、同額・反対向き・同日〜翌日の明細
- aggregate: 同じ種類の対象 2〜4 件の合計が金額に一致
"""
from __future__ import annotations

import itertools
import re
import unicodedata
from datetime import date, timedelta
from decimal import Decimal
from difflib import SequenceMatcher

from django.db.models import Q, Sum

from apps.core.money import ZERO

from .models import BankReconciliation, BankTransaction, MatchType, RecStatus

FEE_TOLERANCE_RATE = Decimal("0.05")
FEE_TOLERANCE_MIN = Decimal("1100")
_STRIP = ("株式会社", "有限会社", "合同会社", "(株)", "(有)", "（株）", "（有）", "カ)", "カ）", "(カ", "（カ", "ユ)", "ユ）", "(ユ", "ド)", "ド）", "(ド",
          "ｶ)", "ﾕ)", "ﾄﾞ)", "振込", "フリコミ", "ﾌﾘｺﾏ", "ﾌﾘｼﾞ", "振替", "口座振替", "デビット", "ＡＴＭ", "ATM")


def normalize_name(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "")
    for t in _STRIP:
        s = s.replace(unicodedata.normalize("NFKC", t), " ")
    s = re.sub(r"[\s\-_/・.,、。()（）\[\]【】「」\"'’`*＊:：;；]+", "", s)
    # カタカナ → ひらがな
    s = "".join(chr(ord(ch) - 0x60) if "ァ" <= ch <= "ヶ" else ch for ch in s)
    return s.lower()


def similarity(a: str, b: str) -> float:
    na, nb = normalize_name(a), normalize_name(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    if na in nb or nb in na:
        return 0.85
    if na[:3] == nb[:3]:
        return max(0.6, SequenceMatcher(None, na, nb).ratio())
    return SequenceMatcher(None, na, nb).ratio()


def _reconciled_ids(match_type: str, field: str) -> set[int]:
    return set(BankReconciliation.objects.filter(match_type=match_type, status=RecStatus.CONFIRMED, **{f"{field}__isnull": False}).values_list(field, flat=True))


def _store_ids(tp) -> list[int]:
    from apps.accounting.models import StoreTaxProfile

    return list(StoreTaxProfile.objects.filter(taxpayer=tp).values_list("store_id", flat=True))


def _cand(match_type: str, *, target_id=None, amount: Decimal, fee: Decimal = ZERO, rule: str, score: float, label: str, **extra) -> dict:
    d = {"match_type": match_type, "target_id": target_id, "amount": str(amount), "fee_amount": str(fee), "rule": rule, "score": round(score, 2), "label": label}
    d.update(extra)
    return d


def _windows(end: date) -> list[tuple[str, date, date]]:
    """入金日より前の候補期間（1日・週・半月・月）。"""
    out: list[tuple[str, date, date]] = []
    for back in range(1, 46):
        d = end - timedelta(days=back)
        out.append(("day", d, d))
    for back in range(0, 8):
        monday = end - timedelta(days=end.weekday()) - timedelta(days=7 * (back + 1))
        out.append(("week", monday, monday + timedelta(days=6)))
    for back in range(0, 4):
        y, m = end.year, end.month - back
        while m <= 0:
            m += 12
            y -= 1
        first = date(y, m, 1)
        mid = date(y, m, 15)
        last = (date(y + 1, 1, 1) if m == 12 else date(y, m + 1, 1)) - timedelta(days=1)
        out.append(("half", first, mid))
        out.append(("half", mid + timedelta(days=1), last))
        out.append(("month", first, last))
    return [w for w in out if w[2] < end]


def card_candidates(tx: BankTransaction, tp) -> list[dict]:
    from apps.accounting.models import CardSettlement
    from apps.sales.models import Payment, Refund

    if not tx.amount_in:
        return []
    out = []
    amount = tx.amount_in
    tol = max(FEE_TOLERANCE_MIN, (amount * FEE_TOLERANCE_RATE).quantize(Decimal("1")))
    seen: set[tuple] = set()
    for store_id in _store_ids(tp):
        settled = list(CardSettlement.objects.filter(taxpayer=tp, store_id=store_id, is_voided=False, period_start__isnull=False).values_list("period_start", "period_end"))
        for kind, s, e in _windows(tx.date):
            if any(ps <= e and pe >= s for ps, pe in settled):
                continue
            total = Payment.objects.filter(store_id=store_id, method="card", business_date__gte=s, business_date__lte=e).exclude(status="voided").aggregate(t=Sum("amount"))["t"] or ZERO
            total -= Refund.objects.filter(store_id=store_id, method="card", business_date__gte=s, business_date__lte=e).aggregate(t=Sum("amount"))["t"] or ZERO
            if total <= 0:
                continue
            diff = total - amount
            if diff < 0 or diff > tol:
                continue
            k = (store_id, total)
            if k in seen:
                continue
            seen.add(k)
            score = 0.9 if diff == 0 else 0.75 - min(0.2, float(diff / total))
            out.append(_cand(MatchType.CARD_SETTLEMENT, amount=total, fee=diff, rule=f"card_{kind}_{'exact' if diff == 0 else 'fee'}", score=score,
                             label=f"カード売上 {s:%m/%d}〜{e:%m/%d} 合計 {total:,.0f}円" + (f"（手数料 {diff:,.0f}円）" if diff else ""),
                             store_id=store_id, period_start=s.isoformat(), period_end=e.isoformat()))
    out.sort(key=lambda c: -c["score"])
    return out[:5]


def _date_score(d1: date, d2: date, window: int) -> float:
    return max(0.0, 1 - abs((d1 - d2).days) / (window + 1))


def simple_candidates(tx: BankTransaction, tp) -> list[dict]:
    from apps.accounting.models import Expense, OwnerFundTransfer, TaxRemittance
    from apps.cash.models import BankDeposit
    from apps.staff.models import EmployeePayment

    out = []
    stores = _store_ids(tp)
    if tx.amount_in:
        amt = tx.amount_in
        done = _reconciled_ids(MatchType.BANK_DEPOSIT, "bank_deposit_id")
        for dep in BankDeposit.objects.filter(store_id__in=stores, amount=amt, business_day__date__gte=tx.date - timedelta(days=3),
                                              business_day__date__lte=tx.date + timedelta(days=3)).select_related("business_day", "store"):
            if dep.id in done:
                continue
            out.append(_cand(MatchType.BANK_DEPOSIT, target_id=dep.id, amount=amt, rule="deposit_exact", score=0.85 + 0.1 * _date_score(dep.business_day.date, tx.date, 3),
                             label=f"{dep.store.name} {dep.business_day.date} 売上金の預入 {amt:,.0f}円", store_id=dep.store_id))
        done = _reconciled_ids(MatchType.OWNER_TRANSFER, "owner_transfer_id")
        for t in OwnerFundTransfer.objects.filter(taxpayer=tp, direction="in", method="bank", amount=amt, is_voided=False,
                                                  date__gte=tx.date - timedelta(days=7), date__lte=tx.date + timedelta(days=7)):
            if t.id in done:
                continue
            out.append(_cand(MatchType.OWNER_TRANSFER, target_id=t.id, amount=amt, rule="owner_in_exact", score=0.8 + 0.1 * _date_score(t.date, tx.date, 7),
                             label=f"{t.date} 経営者からの資金投入 {amt:,.0f}円"))
    else:
        amt = tx.amount_out
        done = _reconciled_ids(MatchType.EMPLOYEE_PAYMENT, "employee_payment_id")
        for ep in EmployeePayment.objects.filter(store_id__in=stores, method="bank", amount=amt, is_voided=False,
                                                 business_date__gte=tx.date - timedelta(days=7), business_date__lte=tx.date + timedelta(days=7)).select_related("employee"):
            if ep.id in done:
                continue
            sim = similarity(tx.counterparty or tx.description, ep.employee.full_name) if (tx.counterparty or tx.description) else 0
            out.append(_cand(MatchType.EMPLOYEE_PAYMENT, target_id=ep.id, amount=amt, rule="employee_exact", score=0.75 + 0.15 * sim + 0.1 * _date_score(ep.business_date, tx.date, 7),
                             label=f"{ep.business_date} {ep.employee.display_name} への振込 {amt:,.0f}円", store_id=ep.store_id))
        done = _reconciled_ids(MatchType.EXPENSE, "expense_id")
        for e in Expense.objects.filter(taxpayer=tp, paid_from="bank", amount=amt, is_voided=False, date__gte=tx.date - timedelta(days=7), date__lte=tx.date + timedelta(days=7)):
            if e.id in done:
                continue
            sim = similarity(tx.counterparty or tx.description, e.counterparty) if e.counterparty else 0
            out.append(_cand(MatchType.EXPENSE, target_id=e.id, amount=amt, rule="expense_exact", score=0.7 + 0.2 * sim + 0.1 * _date_score(e.date, tx.date, 7),
                             label=f"{e.date} 経費 {e.counterparty} {e.description} {amt:,.0f}円".replace("  ", " "), store_id=e.store_id))
        try:
            from apps.expenses.models import PayablePayment

            done_pp = set(BankReconciliation.objects.filter(match_type=MatchType.PAYABLE_PAYMENT, status=RecStatus.CONFIRMED).values_list("payable_payment_ref", flat=True))
            for pp in PayablePayment.objects.filter(payable__taxpayer=tp, method="bank", amount=amt, is_voided=False,
                                                    date__gte=tx.date - timedelta(days=7), date__lte=tx.date + timedelta(days=7)).select_related("payable"):
                if pp.id in done_pp:
                    continue
                out.append(_cand(MatchType.PAYABLE_PAYMENT, target_id=pp.id, amount=amt, rule="payable_exact", score=0.75 + 0.1 * _date_score(pp.date, tx.date, 7),
                                 label=f"{pp.date} 買掛金の支払い {pp.payable.counterparty} {amt:,.0f}円"))
        except Exception:  # noqa: BLE001 - expenses アプリが無い／形が違う間は候補を出さない
            pass
        done = _reconciled_ids(MatchType.OWNER_TRANSFER, "owner_transfer_id")
        for t in OwnerFundTransfer.objects.filter(taxpayer=tp, direction="out", method="bank", amount=amt, is_voided=False,
                                                  date__gte=tx.date - timedelta(days=7), date__lte=tx.date + timedelta(days=7)):
            if t.id in done:
                continue
            out.append(_cand(MatchType.OWNER_TRANSFER, target_id=t.id, amount=amt, rule="owner_out_exact", score=0.8, label=f"{t.date} 経営者への資金引出し {amt:,.0f}円"))
        done = _reconciled_ids(MatchType.TAX_REMITTANCE, "tax_remittance_id")
        for r in TaxRemittance.objects.filter(taxpayer=tp, paid_from="bank", amount=amt, is_voided=False, paid_on__gte=tx.date - timedelta(days=7), paid_on__lte=tx.date + timedelta(days=7)):
            if r.id in done:
                continue
            out.append(_cand(MatchType.TAX_REMITTANCE, target_id=r.id, amount=amt, rule="remittance_exact", score=0.85, label=f"{r.paid_on} 源泉所得税の納付 {amt:,.0f}円"))
    return out


def transfer_candidates(tx: BankTransaction, tp) -> list[dict]:
    amt = tx.amount
    q = BankTransaction.objects.filter(bank_account__taxpayer=tp, status__in=["unmatched", "suggested"]).exclude(bank_account_id=tx.bank_account_id)
    q = q.filter(date__gte=tx.date - timedelta(days=1), date__lte=tx.date + timedelta(days=1))
    q = q.filter(amount_out=amt) if tx.amount_in else q.filter(amount_in=amt)
    return [
        _cand(MatchType.TRANSFER, target_id=o.id, amount=amt, rule="transfer_same_amount", score=0.8 + 0.1 * _date_score(o.date, tx.date, 1),
              label=f"{o.bank_account.name} {o.date} {'出金' if o.amount_out else '入金'} {amt:,.0f}円（自社口座間の振替）")
        for o in q.select_related("bank_account")[:5]
    ]


def aggregate_candidates(tx: BankTransaction, tp) -> list[dict]:
    """同じ種類の対象 2〜4 件の合計が明細の金額に一致するもの（経費・振込の支払い）。"""
    from apps.accounting.models import Expense
    from apps.staff.models import EmployeePayment

    if not tx.amount_out:
        return []
    amt = tx.amount_out
    out = []
    pools = []
    done_e = _reconciled_ids(MatchType.EXPENSE, "expense_id")
    exps = [e for e in Expense.objects.filter(taxpayer=tp, paid_from="bank", is_voided=False, amount__lt=amt, date__gte=tx.date - timedelta(days=45), date__lte=tx.date + timedelta(days=3)).order_by("-date")[:12] if e.id not in done_e]
    pools.append((MatchType.EXPENSE, exps, lambda e: f"{e.date} {e.counterparty} {e.amount:,.0f}円"))
    done_p = _reconciled_ids(MatchType.EMPLOYEE_PAYMENT, "employee_payment_id")
    eps = [p for p in EmployeePayment.objects.filter(store_id__in=_store_ids(tp), method="bank", is_voided=False, amount__lt=amt, business_date__gte=tx.date - timedelta(days=45),
                                                     business_date__lte=tx.date + timedelta(days=3)).select_related("employee").order_by("-business_date")[:12] if p.id not in done_p]
    pools.append((MatchType.EMPLOYEE_PAYMENT, eps, lambda p: f"{p.business_date} {p.employee.display_name} {p.amount:,.0f}円"))
    for mtype, items, lab in pools:
        found = None
        for n in (2, 3, 4):
            for combo in itertools.combinations(items, n):
                if sum((i.amount for i in combo), ZERO) == amt:
                    found = combo
                    break
            if found:
                break
        if found:
            out.append({"match_type": mtype, "target_id": None, "amount": str(amt), "fee_amount": "0", "rule": f"aggregate_{len(found)}", "score": 0.7,
                        "label": f"{len(found)} 件の合計 {amt:,.0f}円: " + " / ".join(lab(i) for i in found),
                        "items": [{"target_id": i.id, "amount": str(i.amount)} for i in found]})
    return out


def candidates_for(tx: BankTransaction) -> list[dict]:
    tp = tx.bank_account.taxpayer
    out = card_candidates(tx, tp) + simple_candidates(tx, tp) + transfer_candidates(tx, tp) + aggregate_candidates(tx, tp)
    out.sort(key=lambda c: -c["score"])
    return out[:10]


def scan(tp, start: date | None = None, end: date | None = None) -> dict:
    """未照合の明細の候補を計算し直して保存する（status=suggested）。確定済みの候補には触らない。"""
    qs = BankTransaction.objects.filter(bank_account__taxpayer=tp, status__in=["unmatched", "suggested"]).select_related("bank_account")
    if start:
        qs = qs.filter(date__gte=start)
    if end:
        qs = qs.filter(date__lte=end)
    scanned = suggested = 0
    for tx in qs:
        scanned += 1
        BankReconciliation.objects.filter(bank_transaction=tx, status=RecStatus.SUGGESTED).delete()
        cands = candidates_for(tx)
        for c in cands:
            if c.get("items"):
                continue  # 合算候補は画面の提案だけ（確定時に items で配分）
            rec = BankReconciliation(bank_transaction=tx, match_type=c["match_type"], amount=Decimal(c["amount"]), fee_amount=Decimal(c["fee_amount"]),
                                     status=RecStatus.SUGGESTED, rule=c["rule"], score=Decimal(str(c["score"])), label=c["label"][:200])
            _set_target(rec, c["match_type"], c.get("target_id"))
            if c.get("store_id"):
                rec.store_id = c["store_id"]
            if c.get("period_start"):
                rec.period_start, rec.period_end = date.fromisoformat(c["period_start"]), date.fromisoformat(c["period_end"])
            rec.save()
        new_status = "suggested" if cands else "unmatched"
        if tx.status != new_status:
            tx.status = new_status
            tx.save(update_fields=["status", "updated_at"])
        if cands:
            suggested += 1
    return {"scanned": scanned, "with_candidates": suggested}


TARGET_FIELD = {
    MatchType.CARD_SETTLEMENT: "card_settlement_id", MatchType.BANK_DEPOSIT: "bank_deposit_id", MatchType.EMPLOYEE_PAYMENT: "employee_payment_id",
    MatchType.EXPENSE: "expense_id", MatchType.PAYABLE_PAYMENT: "payable_payment_ref", MatchType.OWNER_TRANSFER: "owner_transfer_id",
    MatchType.TAX_REMITTANCE: "tax_remittance_id", MatchType.TRANSFER: "counter_transaction_id", MatchType.MANUAL_JOURNAL: "journal_id",
}


def _set_target(rec: BankReconciliation, match_type: str, target_id) -> None:
    f = TARGET_FIELD.get(match_type)
    if f and target_id:
        setattr(rec, f, int(target_id))
