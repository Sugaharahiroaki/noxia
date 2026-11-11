"""
事業所得の集計: 収支内訳書（一般用）・青色申告決算書（一般用）の行を、仕訳行（ledgers.pl_summary）から作る。
行キーは tax_calculation_rules.md §4。期末棚卸高は貸方（売上原価のマイナス）で記録されているので符号を戻して表示する。
"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from django.db.models import Sum

from apps.accounting import ledgers
from apps.accounting.books import ensure_accounts
from apps.accounting.models import AccountCategory, FiscalYear, TaxPayer
from apps.core.money import D, ZERO

REVENUE_LINES = ("sales", "household_consumption", "other_income")
COGS_LINES = ("opening_inventory", "purchases", "closing_inventory")
EXPENSE_LINES = ("salaries", "outsourcing", "depreciation", "bad_debt", "rent", "interest", "taxes_dues", "freight", "utilities", "travel", "communication",
                 "advertising", "entertainment", "insurance", "repair", "supplies", "welfare", "misc_expense")


def business_summary(taxpayer: TaxPayer, fy: FiscalYear, *, family_employee=ZERO, filing_type: str = "white") -> dict:
    """事業収支の集計（店舗別の内訳と事業者合計）。"""
    pl = ledgers.pl_summary(taxpayer, fy.start_date, fy.end_date)
    accounts = ensure_accounts(taxpayer)
    by_key_line = {a.key: (a.statement_line or a.key) for a in accounts.values()}
    by_line: dict[str, Decimal] = defaultdict(lambda: ZERO)
    custom_rows: list[dict] = []
    for group in ("income", "cogs", "expense"):
        for row in pl[group]:
            line = by_key_line.get(row["key"], row["key"])
            amt = D(row["amount"])
            if line == "custom":
                custom_rows.append({"key": row["key"], "name": row["name"], "amount": str(amt)})
            by_line[line] += amt
    sales, household, other = by_line["sales"], by_line["household_consumption"], by_line["other_income"]
    revenue_total = sales + household + other
    opening_inv, purchases = by_line["opening_inventory"], by_line["purchases"]
    closing_inv = -by_line["closing_inventory"]  # 貸方で記録 → 表示は正
    cogs_subtotal = opening_inv + purchases
    cogs = cogs_subtotal - closing_inv
    gross = revenue_total - cogs
    expenses = {k: by_line[k] for k in EXPENSE_LINES}
    custom_total = sum((D(r["amount"]) for r in custom_rows), ZERO)
    other_exp_lines = ("taxes_dues", "freight", "utilities", "travel", "communication", "advertising", "entertainment", "insurance", "repair", "supplies", "welfare", "misc_expense")
    other_expenses_subtotal = sum((expenses[k] for k in other_exp_lines), ZERO) + custom_total
    expenses_total = sum(expenses.values(), ZERO) + custom_total
    income_before_family = gross - expenses_total
    fam = D(family_employee or 0)
    income_before_blue = income_before_family - fam
    values = {
        "sales": sales, "household_consumption": household, "other_income": other, "revenue_total": revenue_total,
        "opening_inventory": opening_inv, "purchases": purchases, "cogs_subtotal": cogs_subtotal, "closing_inventory": closing_inv, "cogs": cogs,
        "gross_profit": gross, **expenses, "custom": custom_total, "other_expenses_subtotal": other_expenses_subtotal, "expenses_total": expenses_total,
        "income_before_family": income_before_family, "family_employee": fam, "income_before_blue": income_before_blue,
    }
    return {
        "from": fy.start_date.isoformat(), "to": fy.end_date.isoformat(), "filing_type": filing_type,
        "values": {k: str(v) for k, v in values.items()},
        "custom_rows": custom_rows,
        "by_store": pl["by_store"],
        "monthly": monthly_sales_purchases(taxpayer, fy),
        "suspense_total": pl["suspense_total"],
        "has_suspense": D(pl["suspense_total"]) != 0,
    }


def monthly_sales_purchases(taxpayer: TaxPayer, fy: FiscalYear) -> list[dict]:
    """青色申告決算書 2 ページ目「月別売上（収入）金額及び仕入金額」。"""
    accounts = ensure_accounts(taxpayer)
    sales_acc, purchases_acc = accounts.get("sales"), accounts.get("purchases")
    out = {}
    d = fy.start_date
    while d <= fy.end_date:
        out[d.strftime("%Y-%m")] = {"month": d.strftime("%Y-%m"), "sales": ZERO, "purchases": ZERO}
        d = (d.replace(day=1) + __import__("datetime").timedelta(days=32)).replace(day=1)
    for acc, key in ((sales_acc, "sales"), (purchases_acc, "purchases")):
        if acc is None:
            continue
        for ln in ledgers.lines_qs(taxpayer, fy.start_date, fy.end_date, account=acc).values("date", "side", "amount"):
            m = ln["date"].strftime("%Y-%m")
            if m not in out:
                continue
            sign = 1 if (ln["side"] == "credit") == (key == "sales") else -1
            out[m][key] += ln["amount"] * sign
    return [{"month": v["month"], "sales": str(v["sales"]), "purchases": str(v["purchases"])} for v in out.values()]


def statement_rows(summary: dict, cfg: dict, filing_type: str, blue_deduction: Decimal, business_income: Decimal) -> list[dict]:
    """様式の行（行番号・項目名・金額）。白色は収支内訳書、青色は決算書。"""
    form_key = "aoiro_kessansho" if filing_type.startswith("blue") else "shushi_uchiwakesho"
    form = cfg["statement_lines"][form_key]
    values = {k: D(v) for k, v in summary["values"].items()}
    values["blue_deduction"] = D(blue_deduction)
    values["business_income"] = D(business_income)
    if form_key == "aoiro_kessansho":
        values["sales"] = values["sales"] + values["household_consumption"] + values["other_income"]  # 決算書①は雑収入を含む
    rows = []
    for no, key, label in form["rows"]:
        if key == "custom":
            for r in summary["custom_rows"]:
                rows.append({"no": no, "key": f"custom:{r['key']}", "label": r["name"], "amount": r["amount"], "custom": True})
            continue
        rows.append({"no": no, "key": key, "label": label, "amount": str(values.get(key, ZERO))})
    return rows
