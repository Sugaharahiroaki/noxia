"""転記用 PDF（独自レイアウト。公式様式への重ね刷りは様式の確認後）。入力ガイドの A4 PDF。"""
from __future__ import annotations

from reportlab.lib.units import mm
from reportlab.platypus import PageBreak, Paragraph, Spacer

from apps.accounting.pdf import _build, _styles, _table, yen

NOTICE = "この書類は NOXIA の集計を国税庁の確定申告書等作成コーナーへ転記するための転記用です。申告書そのものではありません。申告はご本人が国税庁のシステムで行ってください。"


def return_forms_pdf(tr) -> bytes:
    s = _styles()
    c = tr.computed or {}
    year = tr.config.year
    title = f"{year}年分 申告書類 転記用（{'青色申告決算書（一般用）' if c.get('filing_type_applied', '').startswith('blue') else '収支内訳書（一般用）'}・確定申告書）"
    tp = tr.taxpayer
    prof = getattr(tp, "individual_profile", None)
    story = [Paragraph(title, s["title"]), Paragraph(NOTICE, s["small"]),
             Paragraph(f"事業者: {tp.name}　屋号: {getattr(prof, 'trade_name', '') or '—'}　期間: {tr.fiscal_year.start_date} 〜 {tr.fiscal_year.end_date}　"
                       f"計算日時: {str(c.get('computed_at', ''))[:16]}　版: {tr.versions.first().version_no if tr.versions.exists() else '-'}", s["body"]),
             Spacer(1, 3 * mm)]
    if tr.unsupported_items:
        story.append(Paragraph("※ 未対応の項目があるため、この書類は完成ではありません: " + "／".join(u["message"] for u in tr.unsupported_items), s["small"]))
    story.append(Paragraph("第1部 事業収支", s["h2"]))
    rows = [["行", "項目", "金額"]] + [[r["no"], r["label"], yen(r["amount"])] for r in c.get("statement_rows", [])]
    story.append(_table(rows, col_widths=[18 * mm, 110 * mm, 40 * mm], number_cols=[2]))
    by_store = (tr.business_summary or {}).get("by_store") or []
    if len(by_store) > 1:
        story.append(Paragraph("店舗別の内訳（管理用）", s["h2"]))
        story.append(_table([["店舗", "収益", "費用", "利益"]] + [[b["store"], yen(b["income"]), yen(b["expense"]), yen(b["profit"])] for b in by_store], number_cols=[1, 2, 3]))
    story.append(PageBreak())
    story.append(Paragraph("第2部 所得・所得控除・税額", s["h2"]))
    ded = [["所得控除", "金額", "根拠"]] + [[d["label"], yen(d["amount"]), Paragraph(d.get("basis", ""), s["cell"])] for d in c.get("deductions", [])]
    story.append(_table(ded, col_widths=[60 * mm, 35 * mm, 75 * mm], number_cols=[1]))
    story.append(Spacer(1, 3 * mm))
    tr_rows = [["項目", "金額", "根拠・端数処理"]] + [[t["label"], yen(t["value"]), Paragraph(t.get("basis", ""), s["cell"])] for t in c.get("trace", [])]
    story.append(_table(tr_rows, col_widths=[60 * mm, 35 * mm, 75 * mm], number_cols=[1]))
    story.append(Spacer(1, 3 * mm))
    for w in tr.warnings or []:
        story.append(Paragraph("※ " + w, s["small"]))
    story.append(Paragraph(c.get("notice", ""), s["small"]))
    return _build(story, f"{year}年分 申告書類 転記用")


def guide_pdf(guide: dict, taxpayer_name: str) -> bytes:
    s = _styles()
    story = [Paragraph(f"{guide['year']}年分 国税庁 確定申告書等作成コーナー かんたん入力ガイド（{'青色' if guide['filing'] == 'blue' else '白色'}）", s["title"]),
             Paragraph(f"事業者: {taxpayer_name}　" + guide.get("notice", ""), s["small"]), Spacer(1, 3 * mm)]
    for sec in guide["sections"]:
        story.append(Paragraph(sec["label"], s["h2"]))
        story.append(Paragraph(sec.get("intro", ""), s["small"]))
        rows = [["✓", "項目", "金額・内容", "入力先（作成コーナー）"]]
        for it in guide["items"]:
            if it["section"] != sec["key"]:
                continue
            v = it.get("value")
            if isinstance(v, (list, dict)):
                vs = "（内訳は画面を参照）"
            elif v in (None, ""):
                vs = "—"
            else:
                try:
                    vs = yen(v)
                except Exception:  # noqa: BLE001
                    vs = str(v)
            target = it.get("official_label") or "要確認（公式画面の項目名）"
            rows.append(["■" if it["checked"] else "□", Paragraph(it["label"], s["cell"]), vs, Paragraph(f"{target}　{it.get('help', '')}", s["cell"])])
        story.append(_table(rows, col_widths=[8 * mm, 60 * mm, 32 * mm, 82 * mm], number_cols=[2]))
    story.append(Spacer(1, 3 * mm))
    for ln in guide.get("links", []):
        story.append(Paragraph(f"{ln['label']}: {ln['url']}", s["small"]))
    return _build(story, f"{guide['year']}年分 入力ガイド")
