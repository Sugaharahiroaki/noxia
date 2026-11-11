"""申告書データの XLSX（収支内訳書／決算書の行・所得控除・計算過程・ガイド金額一覧・店舗別・月別）。数式は書かない。"""
from __future__ import annotations

from decimal import Decimal

from apps.accounting.exports import xlsx_bytes


def _d(v):
    try:
        return Decimal(str(v))
    except Exception:  # noqa: BLE001
        return v


def return_xlsx(tr, guide: dict | None = None) -> bytes:
    c = tr.computed or {}
    summary = tr.business_summary or {}
    sheets = []
    sheets.append(("事業収支", ["行", "項目", "金額"], [[r["no"], r["label"], _d(r["amount"])] for r in c.get("statement_rows", [])]))
    sheets.append(("所得控除", ["控除", "金額", "根拠"], [[d["label"], _d(d["amount"]), d.get("basis", "")] for d in c.get("deductions", [])]))
    sheets.append(("計算過程", ["項目", "金額", "根拠"], [[t["label"], _d(t["value"]), t.get("basis", "")] for t in c.get("trace", [])]))
    sheets.append(("店舗別損益", ["店舗", "収益", "費用", "利益"], [[b["store"], _d(b["income"]), _d(b["expense"]), _d(b["profit"])] for b in summary.get("by_store", [])]))
    sheets.append(("月別売上仕入", ["月", "売上", "仕入"], [[m["month"], _d(m["sales"]), _d(m["purchases"])] for m in summary.get("monthly", [])]))
    if guide:
        rows = []
        for it in guide["items"]:
            v = it.get("value")
            rows.append([it["section"], it["label"], _d(v) if not isinstance(v, (list, dict)) and v not in (None, "") else "", it.get("official_label") or "要確認", "済" if it["checked"] else ""])
        sheets.append(("入力ガイド", ["部", "項目", "金額", "作成コーナーの項目名", "入力済"], rows))
    if tr.unsupported_items or tr.warnings:
        rows = [["未対応", u["message"]] for u in tr.unsupported_items] + [["注意", w] for w in tr.warnings]
        sheets.append(("注意", ["種類", "内容"], rows))
    return xlsx_bytes(sheets)
