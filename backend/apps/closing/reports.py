"""年間決算の集計と出力（年間損益・月次損益・店舗別・決算整理一覧・固定資産台帳）。"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal

from apps.accounting import books, ledgers
from apps.accounting.books import STATEMENT_LINE_LABELS
from apps.accounting.models import AccountCategory, FiscalYear, TaxPayer
from apps.core.money import D, ZERO

from . import adjustments, depreciation
from .models import ClosingAdjustment, DepreciationEntry


def _months(fy: FiscalYear) -> list[tuple[int, int]]:
    out = []
    y, m = fy.start_date.year, fy.start_date.month
    for _ in range(12):
        out.append((y, m))
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return out


def year_pl(tp: TaxPayer, fy: FiscalYear, *, by: str = "taxpayer", store_id: int | None = None) -> dict:
    """年間損益。収支内訳書の行キー別・科目別・店舗別・月別。"""
    summary = ledgers.pl_summary(tp, fy.start_date, fy.end_date, store_id=store_id)
    accounts = books.ensure_accounts(tp)
    pl_ids = {a.id: a for a in accounts.values() if a.category in books.PL_CATEGORIES}
    monthly: dict[tuple[str, str], Decimal] = defaultdict(lambda: ZERO)  # (statement_line, "YYYY-MM") → 金額
    month_totals: dict[str, dict] = defaultdict(lambda: {"income": ZERO, "cogs": ZERO, "expense": ZERO})
    qs = ledgers.lines_qs(tp, fy.start_date, fy.end_date)
    if store_id:
        qs = qs.filter(store_id=store_id)
    for ln in qs.values("account_id", "side", "amount", "date"):
        acc = pl_ids.get(ln["account_id"])
        if acc is None:
            continue
        ym = ln["date"].strftime("%Y-%m")
        if acc.category == AccountCategory.REVENUE:
            v = ln["amount"] if ln["side"] == "credit" else -ln["amount"]
            month_totals[ym]["income"] += v
        else:
            v = ln["amount"] if ln["side"] == "debit" else -ln["amount"]
            month_totals[ym]["cogs" if acc.category == AccountCategory.COGS else "expense"] += v
        monthly[(acc.statement_line or acc.key, ym)] += v
    months = [f"{y}-{m:02d}" for y, m in _months(fy)]
    lines_order = [k for k in STATEMENT_LINE_LABELS] + sorted({k for k, _ in monthly} - set(STATEMENT_LINE_LABELS))
    monthly_rows = []
    for line in lines_order:
        vals = [monthly.get((line, ym), ZERO) for ym in months]
        if not any(vals):
            continue
        monthly_rows.append({"line": line, "label": STATEMENT_LINE_LABELS.get(line, line), "months": [str(v) for v in vals], "total": str(sum(vals, ZERO))})
    month_summary = [{"month": ym, "income": str(month_totals[ym]["income"]), "cogs": str(month_totals[ym]["cogs"]), "expense": str(month_totals[ym]["expense"]),
                      "profit": str(month_totals[ym]["income"] - month_totals[ym]["cogs"] - month_totals[ym]["expense"])} for ym in months]
    adjs = [adjustments.payload(a) for a in ClosingAdjustment.objects.filter(fiscal_year=fy, is_voided=False).select_related("debit_account", "credit_account", "store")]
    deps = [depreciation.entry_payload(e) for e in DepreciationEntry.objects.filter(fiscal_year=fy).select_related("asset__store")]
    return {
        "year": fy.year, "from": fy.start_date.isoformat(), "to": fy.end_date.isoformat(), "filing_type": fy.filing_type, "status": fy.status, "by": by,
        **{k: summary[k] for k in ("income", "cogs", "expense", "by_statement_line", "by_store", "total_income", "total_cogs", "total_expense", "gross_profit", "profit", "suspense_total", "notice")},
        "months": months, "monthly_rows": monthly_rows, "month_summary": month_summary,
        "adjustments": adjs, "depreciation": deps,
        "depreciation_total": str(sum((D(d["amount_business"]) for d in deps), ZERO)),
    }


def pl_sheets(data: dict) -> list[tuple[str, list[str], list]]:
    """XLSX 用: 年間損益・月次損益・店舗別・決算整理一覧・固定資産台帳。"""
    annual = [[r["label"], D(r["amount"])] for r in data["by_statement_line"]]
    annual += [["", None], ["収入合計", D(data["total_income"])], ["売上原価", D(data["total_cogs"])], ["差引金額", D(data["gross_profit"])],
               ["経費合計（売上原価を含む）", D(data["total_expense"])], ["所得金額（控除前）", D(data["profit"])]]
    monthly_header = ["項目"] + data["months"] + ["合計"]
    monthly = [[r["label"]] + [D(v) for v in r["months"]] + [D(r["total"])] for r in data["monthly_rows"]]
    monthly += [["収入合計"] + [D(m["income"]) for m in data["month_summary"]] + [D(data["total_income"])],
                ["売上原価"] + [D(m["cogs"]) for m in data["month_summary"]] + [D(data["total_cogs"])],
                ["経費"] + [D(m["expense"]) for m in data["month_summary"]] + [D(data["total_expense"]) - D(data["total_cogs"])],
                ["利益"] + [D(m["profit"]) for m in data["month_summary"]] + [D(data["profit"])]]
    stores = [[s["store"], D(s["income"]), D(s["expense"]), D(s["profit"])] for s in data["by_store"]]
    adjs = [[a["issue_date"], a["kind_label"], a["description"], a["debit_account"]["name"], a["credit_account"]["name"], D(a["amount"])] for a in data["adjustments"]]
    deps = [[d["asset_name"], d["category"], d["acquired_on"], D(d["acquisition_cost"]), d["method_label"], d["useful_life_years"] or "", d["rate"] or "",
             d["months_in_service"], D(d["amount_total"]), D(d["business_use_percent"]), D(d["amount_business"]), D(d["book_value_end"])] for d in data["depreciation"]]
    return [
        (f"年間損益{data['year']}", ["項目（収支内訳書の行）", "金額"], annual),
        ("月次損益", monthly_header, monthly),
        ("店舗別", ["店舗", "収入", "経費", "利益"], stores),
        ("決算整理一覧", ["日付", "種類", "内容", "借方科目", "貸方科目", "金額"], adjs),
        ("固定資産台帳", ["資産名", "種類", "取得日", "取得価額", "償却方法", "耐用年数", "償却率", "月数", "償却費", "事業割合%", "必要経費算入額", "期末帳簿価額"], deps),
    ]


def pl_pdf(tp: TaxPayer, data: dict) -> bytes:
    from reportlab.lib.units import mm
    from reportlab.platypus import PageBreak, Paragraph, Spacer

    from apps.accounting.pdf import _build, _styles, _table, yen

    s = _styles()
    story = [Paragraph(f"年間損益　{data['year']}年分（{data['from']}〜{data['to']}）　{tp.name}", s["title"]),
             Paragraph(f"申告区分: {'青色申告' if str(data['filing_type']).startswith('blue') else '白色申告'}　年度の状態: {data['status']}", s["body"]), Spacer(1, 3 * mm)]
    rows = [["項目（収支内訳書の行）", "金額"]] + [[r["label"], yen(r["amount"])] for r in data["by_statement_line"]]
    rows += [["収入合計", yen(data["total_income"])], ["売上原価", yen(data["total_cogs"])], ["差引金額", yen(data["gross_profit"])],
             ["経費合計（売上原価を含む）", yen(data["total_expense"])], ["所得金額（青色申告特別控除前）", yen(data["profit"])]]
    story.append(_table(rows, col_widths=[110 * mm, 50 * mm], number_cols=[1]))
    if data["by_store"]:
        story.append(Paragraph("店舗別の内訳", s["h2"]))
        story.append(_table([["店舗", "収入", "経費", "利益"]] + [[st["store"], yen(st["income"]), yen(st["expense"]), yen(st["profit"])] for st in data["by_store"]], number_cols=[1, 2, 3]))
    story.append(PageBreak())
    story.append(Paragraph("月次損益", s["title"]))
    head = ["項目"] + [m[5:] + "月" for m in data["months"]] + ["合計"]
    body = [[r["label"]] + [f"{int(D(v)):,}" if D(v) else "" for v in r["months"]] + [f"{int(D(r['total'])):,}"] for r in data["monthly_rows"]]
    body.append(["利益"] + [f"{int(D(m['profit'])):,}" for m in data["month_summary"]] + [f"{int(D(data['profit'])):,}"])
    story.append(_table([head] + body, col_widths=[38 * mm] + [11 * mm] * 12 + [16 * mm], number_cols=range(1, 14)))
    if data["adjustments"]:
        story.append(Paragraph("決算整理一覧", s["h2"]))
        story.append(_table([["日付", "種類", "内容", "借方", "貸方", "金額"]] + [[str(a["issue_date"]), a["kind_label"][:14], a["description"][:20], a["debit_account"]["name"], a["credit_account"]["name"], yen(a["amount"])] for a in data["adjustments"]], number_cols=[5]))
    if data["depreciation"]:
        story.append(Paragraph("固定資産台帳（減価償却費の計算）", s["h2"]))
        story.append(_table([["資産名", "取得日", "取得価額", "方法", "率", "月数", "償却費", "事業%", "必要経費", "期末帳簿価額"]] +
                            [[d["asset_name"][:16], str(d["acquired_on"]), yen(d["acquisition_cost"]), d["method_label"][:8], d["rate"] or "", d["months_in_service"], yen(d["amount_total"]), d["business_use_percent"], yen(d["amount_business"]), yen(d["book_value_end"])] for d in data["depreciation"]],
                            number_cols=[2, 5, 6, 7, 8, 9]))
    story.append(Spacer(1, 3 * mm))
    story.append(Paragraph(data["notice"], s["small"]))
    return _build(story, f"年間損益 {data['year']}")
