"""
帳簿ビュー。すべて JournalLine（正規化した仕訳行）の絞り込みで作り、帳簿のテーブルは持たない。

- 総勘定元帳（科目別・残高つき）、現金出納帳（店舗別）、預金出納帳（口座別）、売上帳、売掛帳、買掛帳、仕入帳・経費帳、スタッフ支払台帳、
  試算表、損益（収支内訳書の行キー別・店舗別）。
- 残高の符号は科目の正常残高の向き（資産・費用は借方＋、負債・収益は貸方＋）。
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal

from django.db.models import Q, Sum

from apps.core.money import D, ZERO

from .books import PL_CATEGORIES, STATEMENT_LINE_LABELS, TAX_CATEGORIES, ensure_accounts
from .models import Account, AccountCategory, JournalLine, StoreTaxProfile, TaxPayer


def _store_ids(tp: TaxPayer) -> list[int]:
    return list(StoreTaxProfile.objects.filter(taxpayer=tp).values_list("store_id", flat=True))


def lines_qs(tp: TaxPayer, start: date | None = None, end: date | None = None, **filters):
    qs = JournalLine.objects.filter(taxpayer=tp, journal__is_void=False).select_related("account", "journal", "store", "employee")
    if start:
        qs = qs.filter(date__gte=start)
    if end:
        qs = qs.filter(date__lte=end)
    if filters:
        qs = qs.filter(**{k: v for k, v in filters.items() if v is not None})
    return qs


def _signed_sum(qs, account: Account) -> Decimal:
    agg = qs.aggregate(dr=Sum("amount", filter=Q(side="debit")), cr=Sum("amount", filter=Q(side="credit")))
    dr, cr = agg["dr"] or ZERO, agg["cr"] or ZERO
    return (dr - cr) if account.debit_normal else (cr - dr)


def _row(ln: JournalLine, balance: Decimal, counter: str = "") -> dict:
    return {
        "date": ln.date, "journal_id": ln.journal_id, "line_id": ln.id, "description": ln.journal.description, "memo": ln.memo, "counter_accounts": counter,
        "debit": str(ln.amount) if ln.side == "debit" else "0", "credit": str(ln.amount) if ln.side == "credit" else "0", "balance": str(balance),
        "tax_category": ln.tax_category, "tax_label": TAX_CATEGORIES.get(ln.tax_category, ln.tax_category),
        "store": ln.store.name if ln.store else "", "store_id": ln.store_id, "counterparty": ln.counterparty,
        "employee": ln.employee.display_name if ln.employee else "", "entry_kind": ln.journal.entry_kind, "source_type": ln.journal.source_type,
    }


def _counter_names(ln: JournalLine, cache: dict) -> str:
    """同じ仕訳の反対側の科目名（摘要用）。"""
    if ln.journal_id not in cache:
        others = JournalLine.objects.filter(journal_id=ln.journal_id).select_related("account")
        cache[ln.journal_id] = {"debit": sorted({o.account.name for o in others if o.side == "debit"}),
                                "credit": sorted({o.account.name for o in others if o.side == "credit"})}
    opp = "credit" if ln.side == "debit" else "debit"
    names = cache[ln.journal_id][opp]
    return "諸口" if len(names) > 1 else (names[0] if names else "")


def general_ledger(tp: TaxPayer, account_key: str, start: date, end: date, *, store_id: int | None = None, bank_account_ref: int | None = None) -> dict:
    accounts = ensure_accounts(tp)
    acc = accounts.get(account_key)
    if acc is None:
        return {"account": None, "rows": [], "opening": "0", "closing": "0"}
    extra = {}
    if store_id:
        extra["store_id"] = store_id
    if bank_account_ref:
        extra["bank_account_ref"] = bank_account_ref
    opening = _signed_sum(lines_qs(tp, None, start, account=acc, **extra).filter(date__lt=start), acc)
    balance = opening
    rows = []
    cache: dict = {}
    dr_total = cr_total = ZERO
    for ln in lines_qs(tp, start, end, account=acc, **extra).order_by("date", "journal_id", "line_no"):
        signed = ln.amount if (ln.side == "debit") == acc.debit_normal else -ln.amount
        balance += signed
        if ln.side == "debit":
            dr_total += ln.amount
        else:
            cr_total += ln.amount
        rows.append(_row(ln, balance, _counter_names(ln, cache)))
    return {
        "account": {"key": acc.key, "name": acc.name, "category": acc.category, "debit_normal": acc.debit_normal},
        "from": start.isoformat(), "to": end.isoformat(), "opening": str(opening), "closing": str(balance),
        "debit_total": str(dr_total), "credit_total": str(cr_total), "rows": rows,
    }


def cash_book(tp: TaxPayer, start: date, end: date, *, store_id: int | None = None) -> dict:
    accounts = ensure_accounts(tp)
    cash_accounts = [a for a in accounts.values() if a.is_cash]
    data = {"from": start.isoformat(), "to": end.isoformat(), "rows": [], "opening": "0", "closing": "0", "in_total": "0", "out_total": "0"}
    if not cash_accounts:
        return data
    acc_ids = [a.id for a in cash_accounts]
    base = lines_qs(tp, None, None, account_id__in=acc_ids)
    if store_id:
        base = base.filter(store_id=store_id)
    opening_qs = base.filter(date__lt=start)
    agg = opening_qs.aggregate(dr=Sum("amount", filter=Q(side="debit")), cr=Sum("amount", filter=Q(side="credit")))
    balance = (agg["dr"] or ZERO) - (agg["cr"] or ZERO)
    data["opening"] = str(balance)
    rows, cache = [], {}
    in_total = out_total = ZERO
    for ln in base.filter(date__gte=start, date__lte=end).order_by("date", "journal_id", "line_no"):
        if ln.side == "debit":
            balance += ln.amount
            in_total += ln.amount
        else:
            balance -= ln.amount
            out_total += ln.amount
        r = _row(ln, balance, _counter_names(ln, cache))
        r["in"], r["out"] = r.pop("debit"), r.pop("credit")
        rows.append(r)
    data.update({"rows": rows, "closing": str(balance), "in_total": str(in_total), "out_total": str(out_total)})
    return data


def bank_book(tp: TaxPayer, start: date, end: date, *, bank_account_ref: int | None = None) -> dict:
    accounts = ensure_accounts(tp)
    bank_accounts = [a for a in accounts.values() if a.is_bank]
    data = {"from": start.isoformat(), "to": end.isoformat(), "rows": [], "opening": "0", "closing": "0", "in_total": "0", "out_total": "0"}
    if not bank_accounts:
        return data
    base = lines_qs(tp, None, None, account_id__in=[a.id for a in bank_accounts])
    if bank_account_ref:
        base = base.filter(bank_account_ref=bank_account_ref)
    agg = base.filter(date__lt=start).aggregate(dr=Sum("amount", filter=Q(side="debit")), cr=Sum("amount", filter=Q(side="credit")))
    balance = (agg["dr"] or ZERO) - (agg["cr"] or ZERO)
    data["opening"] = str(balance)
    rows, cache = [], {}
    in_total = out_total = ZERO
    for ln in base.filter(date__gte=start, date__lte=end).order_by("date", "journal_id", "line_no"):
        if ln.side == "debit":
            balance += ln.amount
            in_total += ln.amount
        else:
            balance -= ln.amount
            out_total += ln.amount
        r = _row(ln, balance, _counter_names(ln, cache))
        r["in"], r["out"] = r.pop("debit"), r.pop("credit")
        r["bank_account_ref"] = ln.bank_account_ref
        rows.append(r)
    data.update({"rows": rows, "closing": str(balance), "in_total": str(in_total), "out_total": str(out_total)})
    return data


def sales_book(tp: TaxPayer, start: date, end: date, *, store_id: int | None = None, group: str = "day") -> dict:
    """売上帳: POS の元データ（会計・支払・返金）を日別／月別・店舗別に集計し、仕訳の売上と並べる。"""
    from apps.sales.models import Checkout, Payment, Refund

    store_ids = [store_id] if store_id else _store_ids(tp)
    cos = Checkout.objects.filter(store_id__in=store_ids, business_date__gte=start, business_date__lte=end).select_related("store")
    pays = Payment.objects.filter(store_id__in=store_ids, business_date__gte=start, business_date__lte=end).exclude(status="voided")
    refunds = Refund.objects.filter(store_id__in=store_ids, business_date__gte=start, business_date__lte=end)

    def gkey(d: date, sid: int):
        return (d.isoformat() if group == "day" else d.strftime("%Y-%m"), sid)

    rows: dict[tuple, dict] = {}

    def bucket(d, sid, name):
        k = gkey(d, sid)
        if k not in rows:
            rows[k] = {"period": k[0], "store_id": sid, "store": name, "checkouts": 0, "sales_total": ZERO, "tax_10": ZERO, "tax_8": ZERO, "other_rate": ZERO,
                       "cash": ZERO, "card": ZERO, "receivable": ZERO, "refunds": ZERO, "net_sales": ZERO, "journal_sales": ZERO}
        return rows[k]

    store_names = {}
    for co in cos:
        store_names[co.store_id] = co.store.name
        b = bucket(co.business_date, co.store_id, co.store.name)
        b["checkouts"] += 1
        b["sales_total"] += co.total
        allocated = ZERO
        for r in co.tax_breakdown or []:
            amt = D(r.get("total_incl") or 0)
            allocated += amt
            rate = D(r.get("rate") or 10)
            if rate == 10:
                b["tax_10"] += amt
            elif rate == 8:
                b["tax_8"] += amt
            else:
                b["other_rate"] += amt
        b["tax_10"] += co.total - allocated
    for p in pays.select_related("store"):
        b = bucket(p.business_date, p.store_id, p.store.name)
        b[{"cash": "cash", "card": "card", "receivable": "receivable"}.get(p.method, "cash")] += p.amount
    for r in refunds.select_related("store"):
        b = bucket(r.business_date, r.store_id, r.store.name)
        b["refunds"] += r.amount
    # 仕訳側の売上（日次仕訳の売上行）
    accounts = ensure_accounts(tp)
    sales_acc = accounts.get("sales")
    if sales_acc is not None:
        q = lines_qs(tp, start, end, account=sales_acc)
        if store_id:
            q = q.filter(store_id=store_id)
        for ln in q.select_related("store"):
            sid = ln.store_id or 0
            b = bucket(ln.date, sid, ln.store.name if ln.store else "")
            b["journal_sales"] += ln.amount if ln.side == "credit" else -ln.amount
    out = []
    for k in sorted(rows):
        b = rows[k]
        b["net_sales"] = b["sales_total"] - b["refunds"]
        out.append({kk: (str(v) if isinstance(v, Decimal) else v) for kk, v in b.items()})
    totals = defaultdict(lambda: ZERO)
    for b in rows.values():
        for kk, v in b.items():
            if isinstance(v, Decimal):
                totals[kk] += v
    return {"from": start.isoformat(), "to": end.isoformat(), "group": group, "rows": out, "totals": {k: str(v) for k, v in totals.items()}}


def receivables(tp: TaxPayer, start: date, end: date) -> dict:
    """売掛帳: カード売掛（売上−入金＝未入金残高、月別）とツケ（未収）。"""
    from apps.sales.models import Receivable

    accounts = ensure_accounts(tp)
    card = accounts.get("card_receivable")
    rows = []
    balance = ZERO
    if card is not None:
        before = _signed_sum(lines_qs(tp, None, None, account=card).filter(date__lt=start), card)
        balance = before
        by_month: dict[str, dict] = defaultdict(lambda: {"sales": ZERO, "settled": ZERO})
        for ln in lines_qs(tp, start, end, account=card):
            m = ln.date.strftime("%Y-%m")
            if ln.side == "debit":
                by_month[m]["sales"] += ln.amount
            else:
                by_month[m]["settled"] += ln.amount
        for m in sorted(by_month):
            balance += by_month[m]["sales"] - by_month[m]["settled"]
            rows.append({"month": m, "card_sales": str(by_month[m]["sales"]), "settled": str(by_month[m]["settled"]), "balance": str(balance)})
        card_opening = str(before)
    else:
        card_opening = "0"
    from .models import CardSettlement

    settlements = [
        {"id": s.id, "deposit_date": s.deposit_date, "provider": s.provider_name, "sales_amount": str(s.sales_amount), "fee_amount": str(s.fee_amount),
         "deposit_amount": str(s.deposit_amount), "store": s.store.name if s.store else ""}
        for s in CardSettlement.objects.filter(taxpayer=tp, is_voided=False, deposit_date__gte=start, deposit_date__lte=end).select_related("store")
    ]
    tsuke = [
        {"id": r.id, "store": r.store.name, "business_date": r.business_date, "debtor": r.debtor_name or (r.customer.name if r.customer_id else ""),
         "amount": str(r.amount), "collected": str(r.collected_amount), "balance": str(r.amount - r.collected_amount), "status": r.status}
        for r in Receivable.objects.filter(store_id__in=_store_ids(tp)).exclude(status__in=["collected", "cancelled"]).select_related("store", "customer")
    ]
    return {"from": start.isoformat(), "to": end.isoformat(), "card": {"opening": card_opening, "rows": rows, "closing": str(balance), "settlements": settlements},
            "tsuke": tsuke, "tsuke_total": str(sum((D(t["balance"]) for t in tsuke), ZERO))}


def payables(tp: TaxPayer, start: date, end: date) -> dict:
    """買掛帳（apps.expenses.Payable があれば）。"""
    try:
        from apps.expenses.models import Payable
    except Exception:  # noqa: BLE001
        return {"from": start.isoformat(), "to": end.isoformat(), "rows": [], "open_total": "0"}
    rows = []
    open_total = ZERO
    qs = Payable.objects.filter(taxpayer=tp, is_voided=False).filter(Q(invoice_date__lte=end) & (Q(status__in=["open", "partially_paid"]) | Q(invoice_date__gte=start)))
    for p in qs.select_related("supplier", "store").order_by("invoice_date", "id"):
        bal = p.amount - p.paid_amount
        if p.status in ("open", "partially_paid"):
            open_total += bal
        rows.append({"id": p.id, "invoice_date": p.invoice_date, "due_date": p.due_date, "counterparty": p.counterparty or (p.supplier.name if p.supplier_id else ""),
                     "store": p.store.name if p.store else "", "amount": str(p.amount), "paid": str(p.paid_amount), "balance": str(bal), "status": p.status,
                     "description": p.description, "account": p.account.name if p.account_id else ""})
    return {"from": start.isoformat(), "to": end.isoformat(), "rows": rows, "open_total": str(open_total)}


def expense_book(tp: TaxPayer, start: date, end: date, *, account_key: str | None = None, store_id: int | None = None, categories=("expense", "cogs")) -> dict:
    """仕入帳・経費帳: 費用・売上原価の仕訳行を科目別に。account_key を指定すると1科目。"""
    accounts = ensure_accounts(tp)
    acc_ids = [a.id for a in accounts.values() if a.category in categories and (account_key is None or a.key == account_key)]
    qs = lines_qs(tp, start, end, account_id__in=acc_ids)
    if store_id:
        qs = qs.filter(store_id=store_id)
    rows, cache = [], {}
    by_account: dict[str, Decimal] = defaultdict(lambda: ZERO)
    names = {a.id: a for a in accounts.values()}
    for ln in qs.order_by("date", "journal_id", "line_no"):
        acc = names[ln.account_id]
        signed = ln.amount if ln.side == "debit" else -ln.amount
        by_account[acc.key] += signed
        r = _row(ln, ZERO, _counter_names(ln, cache))
        r["account"], r["account_key"], r["amount"] = acc.name, acc.key, str(signed)
        r.pop("balance", None)
        rows.append(r)
    summary = [{"key": k, "name": accounts[k].name, "statement_line": accounts[k].statement_line, "amount": str(v)} for k, v in sorted(by_account.items(), key=lambda x: -x[1])]
    return {"from": start.isoformat(), "to": end.isoformat(), "rows": rows, "by_account": summary, "total": str(sum(by_account.values(), ZERO))}


def staff_payments_book(tp: TaxPayer, start: date, end: date, *, employee_id: int | None = None) -> dict:
    from apps.staff.models import EmployeePayment

    qs = EmployeePayment.objects.filter(store_id__in=_store_ids(tp), business_date__gte=start, business_date__lte=end).select_related("employee", "store", "withholding")
    if employee_id:
        qs = qs.filter(employee_id=employee_id)
    rows = []
    totals = defaultdict(lambda: ZERO)
    for p in qs.order_by("business_date", "paid_at"):
        rec = getattr(p, "withholding", None)
        gross = p.amount + p.withholding_amount + p.deduction_amount
        cat = rec.tax_category if rec else "undetermined"
        rows.append({"date": p.business_date, "paid_at": p.paid_at, "store": p.store.name, "employee_id": p.employee_id, "employee": p.employee.display_name,
                     "kind": p.get_kind_display(), "method": p.method, "gross": str(gross), "withholding": str(p.withholding_amount),
                     "deduction": str(p.deduction_amount), "net": str(p.amount), "tax_category": cat, "is_voided": p.is_voided,
                     "status": rec.status if rec else ""})
        if not p.is_voided:
            totals["gross"] += gross
            totals["withholding"] += p.withholding_amount
            totals["deduction"] += p.deduction_amount
            totals["net"] += p.amount
    by_employee: dict[int, dict] = {}
    for r in rows:
        if r["is_voided"]:
            continue
        b = by_employee.setdefault(r["employee_id"], {"employee_id": r["employee_id"], "employee": r["employee"], "gross": ZERO, "withholding": ZERO, "net": ZERO, "count": 0})
        b["gross"] += D(r["gross"])
        b["withholding"] += D(r["withholding"])
        b["net"] += D(r["net"])
        b["count"] += 1
    return {"from": start.isoformat(), "to": end.isoformat(), "rows": rows, "totals": {k: str(v) for k, v in totals.items()},
            "by_employee": [{**b, "gross": str(b["gross"]), "withholding": str(b["withholding"]), "net": str(b["net"]),
                             "over_500k": b["gross"] > Decimal(500000)} for b in by_employee.values()]}


def trial_balance(tp: TaxPayer, start: date, end: date) -> dict:
    accounts = ensure_accounts(tp)
    agg = lines_qs(tp, start, end).values("account_id").annotate(dr=Sum("amount", filter=Q(side="debit")), cr=Sum("amount", filter=Q(side="credit")))
    by_id = {a["account_id"]: a for a in agg}
    rows = []
    for a in sorted(accounts.values(), key=lambda x: (x.sort_order, x.code)):
        row = by_id.get(a.id)
        if not row:
            continue
        dr, cr = row["dr"] or ZERO, row["cr"] or ZERO
        rows.append({"key": a.key, "name": a.name, "category": a.category, "debit": str(dr), "credit": str(cr),
                     "balance": str((dr - cr) if a.debit_normal else (cr - dr))})
    return {"from": start.isoformat(), "to": end.isoformat(), "rows": rows}


def pl_summary(tp: TaxPayer, start: date, end: date, *, store_id: int | None = None) -> dict:
    """損益: 収益・売上原価・経費の科目別、収支内訳書の行キー別、店舗別。仮勘定があれば未確定。"""
    accounts = ensure_accounts(tp)
    pl_ids = {a.id: a for a in accounts.values() if a.category in PL_CATEGORIES}
    qs = lines_qs(tp, start, end)
    if store_id:
        qs = qs.filter(store_id=store_id)
    income: dict[str, Decimal] = defaultdict(lambda: ZERO)
    cogs: dict[str, Decimal] = defaultdict(lambda: ZERO)
    expense: dict[str, Decimal] = defaultdict(lambda: ZERO)
    by_line: dict[str, Decimal] = defaultdict(lambda: ZERO)
    by_store: dict[int, dict] = defaultdict(lambda: {"income": ZERO, "expense": ZERO})
    suspense = ZERO
    suspense_ids = {a.id for a in accounts.values() if a.key.startswith("suspense")}
    for ln in qs.values("account_id", "side", "amount", "store_id"):
        acc = pl_ids.get(ln["account_id"])
        if acc is None:
            if ln["account_id"] in suspense_ids:
                suspense += ln["amount"]
            continue
        amt = ln["amount"]
        if acc.category == AccountCategory.REVENUE:
            v = amt if ln["side"] == "credit" else -amt
            income[acc.key] += v
            by_store[ln["store_id"] or 0]["income"] += v
        else:
            v = amt if ln["side"] == "debit" else -amt
            (cogs if acc.category == AccountCategory.COGS else expense)[acc.key] += v
            by_store[ln["store_id"] or 0]["expense"] += v
        by_line[acc.statement_line or acc.key] += v
    total_income = sum(income.values(), ZERO)
    total_cogs = sum(cogs.values(), ZERO)
    total_expense = sum(expense.values(), ZERO)
    from apps.core.models import Store

    names = {s.id: s.name for s in Store.objects.filter(id__in=[k for k in by_store if k])}
    return {
        "from": start.isoformat(), "to": end.isoformat(), "store_id": store_id,
        "income": [{"key": k, "name": accounts[k].name, "amount": str(v)} for k, v in income.items()],
        "cogs": [{"key": k, "name": accounts[k].name, "amount": str(v)} for k, v in cogs.items()],
        "expense": [{"key": k, "name": accounts[k].name, "amount": str(v)} for k, v in sorted(expense.items(), key=lambda x: -x[1])],
        "by_statement_line": [{"line": k, "label": STATEMENT_LINE_LABELS.get(k, k), "amount": str(v)} for k, v in by_line.items()],
        "by_store": [{"store_id": sid, "store": names.get(sid, "（店舗なし）"), "income": str(v["income"]), "expense": str(v["expense"]), "profit": str(v["income"] - v["expense"])}
                     for sid, v in sorted(by_store.items())],
        "total_income": str(total_income), "total_cogs": str(total_cogs), "total_expense": str(total_expense + total_cogs),
        "gross_profit": str(total_income - total_cogs), "profit": str(total_income - total_cogs - total_expense),
        "suspense_total": str(suspense),
        "notice": "締め済みの営業日と登録済みの経費・支払い・決算整理から作った仕訳の集計です。未登録の経費は含みません。仮勘定がある場合は損益が確定していません。",
    }


# --- 出力用の表 -------------------------------------------------------------------------
def ledger_table(kind: str, data: dict) -> tuple[str, list[str], list[list]]:
    """CSV/XLSX 用の（シート名・見出し・行）。"""
    if kind in ("general", "cash", "bank"):
        inkey, outkey = ("in", "out") if kind in ("cash", "bank") else ("debit", "credit")
        header = ["日付", "摘要", "相手科目", "店舗", "取引先", "入金" if kind != "general" else "借方", "出金" if kind != "general" else "貸方", "残高", "税区分", "仕訳ID"]
        rows = [[r["date"], r["description"] + (f" {r['memo']}" if r["memo"] else ""), r["counter_accounts"], r["store"], r["counterparty"],
                 D(r[inkey]), D(r[outkey]), D(r["balance"]), r["tax_label"], r["journal_id"]] for r in data["rows"]]
        rows.insert(0, ["", "前期繰越（期首残高）", "", "", "", 0, 0, D(data["opening"]), "", ""])
        return {"general": "総勘定元帳", "cash": "現金出納帳", "bank": "預金出納帳"}[kind], header, rows
    if kind == "sales":
        header = ["期間", "店舗", "会計数", "売上（税込）", "10%対象", "8%対象", "現金", "カード", "ツケ", "返金", "純売上", "仕訳の売上"]
        rows = [[r["period"], r["store"], r["checkouts"], D(r["sales_total"]), D(r["tax_10"]), D(r["tax_8"]), D(r["cash"]), D(r["card"]), D(r["receivable"]),
                 D(r["refunds"]), D(r["net_sales"]), D(r["journal_sales"])] for r in data["rows"]]
        return "売上帳", header, rows
    if kind == "receivables":
        header = ["月", "カード売上", "入金（消込）", "未入金残高"]
        rows = [[r["month"], D(r["card_sales"]), D(r["settled"]), D(r["balance"])] for r in data["card"]["rows"]]
        return "売掛帳（カード）", header, rows
    if kind == "payables":
        header = ["請求日", "支払期日", "取引先", "店舗", "科目", "内容", "金額", "支払済", "残高", "状態"]
        rows = [[r["invoice_date"], r["due_date"], r["counterparty"], r["store"], r["account"], r["description"], D(r["amount"]), D(r["paid"]), D(r["balance"]), r["status"]] for r in data["rows"]]
        return "買掛帳", header, rows
    if kind in ("expenses", "purchases"):
        header = ["日付", "科目", "摘要", "取引先", "店舗", "金額", "税区分", "相手科目", "仕訳ID"]
        rows = [[r["date"], r["account"], r["description"] + (f" {r['memo']}" if r["memo"] else ""), r["counterparty"], r["store"], D(r["amount"]), r["tax_label"], r["counter_accounts"], r["journal_id"]] for r in data["rows"]]
        return "仕入帳" if kind == "purchases" else "経費帳", header, rows
    if kind == "staff_payments":
        header = ["営業日", "店舗", "スタッフ", "種別", "方法", "報酬総額", "源泉所得税", "その他控除", "手渡し・振込額", "税務区分", "取消"]
        rows = [[r["date"], r["store"], r["employee"], r["kind"], r["method"], D(r["gross"]), D(r["withholding"]), D(r["deduction"]), D(r["net"]), r["tax_category"], "取消" if r["is_voided"] else ""] for r in data["rows"]]
        return "スタッフ支払台帳", header, rows
    if kind == "trial_balance":
        header = ["科目", "区分", "借方合計", "貸方合計", "残高"]
        rows = [[r["name"], r["category"], D(r["debit"]), D(r["credit"]), D(r["balance"])] for r in data["rows"]]
        return "試算表", header, rows
    raise ValueError(kind)
