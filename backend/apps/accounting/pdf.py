"""
PDF 出力（reportlab）。日本語は reportlab 内蔵の CID フォント（HeiseiKakuGo-W5）を使い、フォントファイルを同梱しない。

- 日払い精算書（店舗の1日分の一覧＋キャストごとの精算書）
- 源泉所得税の月次集計
- 所得税徴収高計算書への転記用（e-Tax・納付書に書き写す項目。e-Tax に取り込める形式ではない）
"""
from __future__ import annotations

import io

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from apps.core.money import D

FONT = "HeiseiKakuGo-W5"
_registered = False


def _font():
    global _registered
    if not _registered:
        pdfmetrics.registerFont(UnicodeCIDFont(FONT))
        _registered = True
    return FONT


def _styles():
    f = _font()
    return {
        "title": ParagraphStyle("title", fontName=f, fontSize=15, leading=20, spaceAfter=6),
        "h2": ParagraphStyle("h2", fontName=f, fontSize=11.5, leading=16, spaceBefore=8, spaceAfter=4),
        "body": ParagraphStyle("body", fontName=f, fontSize=9, leading=13),
        "small": ParagraphStyle("small", fontName=f, fontSize=7.5, leading=10, textColor=colors.HexColor("#444444")),
        "cell": ParagraphStyle("cell", fontName=f, fontSize=8, leading=10),
    }


def yen(v) -> str:
    try:
        return f"¥{int(D(v)):,}"
    except Exception:
        return str(v or "")


def _table(data, col_widths=None, number_cols=(), header=True):
    t = Table(data, colWidths=col_widths, repeatRows=1 if header else 0)
    style = [
        ("FONTNAME", (0, 0), (-1, -1), _font()),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#999999")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]
    if header:
        style.append(("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EEEEEE")))
    for c in number_cols:
        style.append(("ALIGN", (c, 1 if header else 0), (c, -1), "RIGHT"))
    t.setStyle(TableStyle(style))
    return t


def _build(story, title: str) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=14 * mm, rightMargin=14 * mm, topMargin=14 * mm, bottomMargin=14 * mm, title=title)

    def footer(canvas, doc_):
        canvas.saveState()
        canvas.setFont(_font(), 7)
        canvas.drawRightString(A4[0] - 14 * mm, 8 * mm, f"NOXIA ・ {title} ・ {doc_.page}ページ")
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()


def _minutes(m) -> str:
    m = int(m or 0)
    return f"{m // 60}時間{m % 60:02d}分"


def daily_payout_pdf(store_name: str, work_date: str, rows: list[dict], *, single: bool = False) -> bytes:
    s = _styles()
    story = []
    paid_rows = [r for r in rows if r["status"] != "absent"]
    if not single:
        story += [Paragraph(f"日払い精算一覧　{store_name}　{work_date} 営業分", s["title"])]
        data = [["キャスト", "勤務時間", "基本報酬", "各種バック", "報酬総額", "源泉所得税", "その他控除", "差引支給額", "支払額", "未払残高"]]
        for r in paid_rows:
            backs = D(r["back_confirmed"])
            remaining = D(r["remaining_amount"]) if D(r["remaining_gross"]) > 0 else D(0)
            data.append([
                Paragraph(r["employee_name"], s["cell"]), _minutes(r["worked_minutes"]), yen(r["wage_amount"]), yen(backs), yen(r["gross_amount"]),
                yen(r["withheld_amount"]), yen(r["deducted_amount"]), yen(D(r["gross_amount"]) - D(r["withheld_amount"]) - D(r["deducted_amount"])),
                yen(r["paid_amount"]), yen(remaining),
            ])
        story.append(_table(data, col_widths=[30 * mm, 18 * mm] + [16.5 * mm] * 8, number_cols=range(2, 10)))
        story.append(Spacer(1, 4 * mm))
        story.append(Paragraph("源泉所得税は預り金として管理し、支払った月の翌月10日までに納付します。差引支給額は源泉所得税・その他控除を差し引いた額です。"
                               "未払残高は、源泉所得税・その他控除を差し引いた後の、まだ渡していない額です。", s["small"]))
    for i, r in enumerate(paid_rows):
        if not single or i:
            story.append(PageBreak())
        story += _statement(store_name, work_date, r, s)
    if not story:
        story.append(Paragraph("対象の支払いはありません。", s["body"]))
    return _build(story, f"日払い精算書 {work_date}")


def _statement(store_name: str, work_date: str, r: dict, s) -> list:
    out = [Paragraph(f"日払い精算書　{r['employee_name']} 様", s["title"]),
           Paragraph(f"{store_name}　{work_date} 営業分（勤務 {_minutes(r['worked_minutes'])}）", s["body"]), Spacer(1, 3 * mm)]
    items = [["報酬項目", "金額", "源泉徴収の対象"]]
    for it in r.get("items") or []:
        items.append([it["label"], yen(it["amount"]), "対象" if it.get("withholding_target") else "対象外"])
    items.append(["報酬総額", yen(r["gross_amount"]), ""])
    out.append(_table(items, col_widths=[80 * mm, 35 * mm, 30 * mm], number_cols=[1]))
    out.append(Spacer(1, 3 * mm))
    pays = [["支払日時", "報酬（精算分）", "源泉所得税", "その他控除", "手渡し額"]]
    for p in r.get("payments") or []:
        gross = D(p["amount"]) + D(p.get("withholding_amount") or 0) + D(p.get("deduction_amount") or 0)
        pays.append([p["paid_at"][:16].replace("T", " "), yen(gross), yen(p.get("withholding_amount") or 0), yen(p.get("deduction_amount") or 0), yen(p["amount"])])
    out.append(_table(pays, col_widths=[40 * mm, 30 * mm, 28 * mm, 28 * mm, 28 * mm], number_cols=[1, 2, 3, 4]))
    deductions = [d for p in r.get("payments") or [] for d in p.get("deduction_items") or []]
    if deductions:
        out.append(Paragraph("その他控除の内訳: " + "、".join(
            f"{d['label']}（{D(d['value']).normalize():f}%）{yen(d['amount'])}" if d.get("calc") == "percent" else f"{d['label']} {yen(d['amount'])}"
            for d in deductions
        ), s["small"]))
    remaining = D(r["remaining_amount"]) if D(r["remaining_gross"]) > 0 else D(0)
    out.append(Spacer(1, 3 * mm))
    out.append(Paragraph(f"未払残高（このあと渡す額）: {yen(remaining)}", s["h2"]))
    tax = r.get("tax") or {}
    if tax.get("status") == "needs_review":
        out.append(Paragraph("※ 税務区分などが確認中のため、源泉所得税を計算していない支払いがあります（あとで確定・精算することがあります）。", s["small"]))
    if tax and not tax.get("withholding_enabled", True):
        out.append(Paragraph("源泉所得税は差し引いていません（店舗設定）。", s["small"]))
        return out
    if tax.get("explanation"):
        out.append(Paragraph("計算: " + tax["explanation"], s["small"]))
    out.append(Paragraph("源泉所得税は、お店が預かって税務署に納付します。", s["small"]))
    return out


def monthly_summary_pdf(view: dict, form: dict) -> bytes:
    s = _styles()
    t = view["totals"]
    story = [
        Paragraph(f"源泉所得税 月次集計　{view['label']}（{view['category_label']}）", s["title"]),
        Paragraph(f"事業者: {view['taxpayer_name']}　納付期限: {view['due_date']}　状態: {view['status_label']}", s["body"]),
        Spacer(1, 3 * mm),
    ]
    if view["category"] == "hostess_fee":
        summary = [
            ["報酬支払総額（要確認を含む）", yen(t["gross_total"])], ["源泉徴収対象額（法定の支払額）", yen(t["target_total"])],
            ["支払額からの控除額", yen(t["deduction_total"])], ["源泉徴収済額", yen(t["withheld_total"])], ["訂正", yen(t["adjustment_total"])],
            ["納付する税額", yen(t["tax_total"])], ["対象人数（実人員）", f"{t['people']}人"], ["支払件数", f"{t['payment_count']}件"],
            ["未確定（要確認）件数", f"{t['needs_review_count']}件"], ["納付済額", yen(view["remitted_total"])],
        ]
    else:
        summary = [["支給額合計", yen(t["gross_total"])], ["源泉徴収税額", yen(t["withheld_total"])], ["訂正", yen(t["adjustment_total"])],
                   ["納付する税額", yen(t["tax_total"])], ["納付済額", yen(view["remitted_total"])]]
    story.append(_table(summary, col_widths=[80 * mm, 50 * mm], number_cols=[1], header=False))
    if view.get("by_store"):
        story.append(Paragraph("店舗別（管理用の内訳）", s["h2"]))
        data = [["店舗", "人数", "件数", "報酬総額", "対象額", "控除額", "源泉所得税"]]
        for r in view["by_store"]:
            data.append([r["store_name"], r["people"], r["count"], yen(r["gross"]), yen(r["target"]), yen(r["deduction"]), yen(r["withholding"])])
        story.append(_table(data, number_cols=range(1, 7)))
    if view.get("by_employee"):
        story.append(Paragraph("キャスト別", s["h2"]))
        data = [["キャスト", "件数", "報酬総額", "対象額", "控除額", "源泉所得税"]]
        for r in view["by_employee"]:
            data.append([r["employee_name"], r["count"], yen(r["gross"]), yen(r["target"]), yen(r["deduction"]), yen(r["withholding"])])
        story.append(_table(data, number_cols=range(1, 6)))
    if view.get("rows"):
        data = [["区分", "人員", "支給額", "税額"]] + [[r["label"], r["people"], yen(r["gross"]), yen(r["withholding"])] for r in view["rows"]]
        story.append(_table(data, number_cols=[1, 2, 3]))
    story.append(Spacer(1, 4 * mm))
    for w in form.get("warnings") or []:
        story.append(Paragraph("※ " + w, s["small"]))
    story.append(Paragraph(form["notice"], s["small"]))
    return _build(story, f"源泉所得税 月次集計 {view['period_start'][:7]}")


def transcription_pdf(form: dict) -> bytes:
    s = _styles()
    tp = form["taxpayer"]
    story = [
        Paragraph(f"{form['form']}　転記用", s["title"]),
        Paragraph(form["notice"], s["small"]),
        Spacer(1, 3 * mm),
    ]
    head = [
        ["年度", form["nendo"], form["nendo_note"]],
        ["税務署名", tp["tax_office_name"] or "（未設定）", ""],
        ["整理番号", tp["reference_number"] or "（未設定）", ""],
        ["納期等の区分", form["nouki"]["code"], form["nouki"]["label"]],
        ["住所（所在地）", tp["address"] or "（未設定）", ""],
        ["氏名又は名称", tp["name"], tp["entity_type"]],
        ["電話番号", tp["phone"], ""],
        ["納期限", form["due_date"], "土日祝日・年末年始に当たる場合は休日明けの日"],
    ]
    story.append(_table([[Paragraph(str(c), s["cell"]) for c in row] for row in head], col_widths=[32 * mm, 50 * mm, 95 * mm], header=False))
    story.append(Spacer(1, 3 * mm))
    data = [["区分", "コード", "人員", "支払額（支給額）", "税額"]]
    for r in form["rows"]:
        data.append([r["label"], r.get("code", ""), f"{r['people']}人" + (f"（{r['people_note']}）" if r.get("people_note") else ""),
                     yen(r["payment_amount"]), yen(r["tax"])])
    data.append(["合計額", "", "", "", yen(form["total"])])
    story.append(_table(data, col_widths=[55 * mm, 15 * mm, 35 * mm, 35 * mm, 35 * mm], number_cols=[3, 4]))
    if form.get("tekiyo"):
        story.append(Paragraph("摘要", s["h2"]))
        story.append(_table([[t["label"], yen(t["amount"])] for t in form["tekiyo"]], col_widths=[80 * mm, 40 * mm], number_cols=[1], header=False))
    story.append(Spacer(1, 3 * mm))
    story.append(Paragraph(form["penalty_note"], s["small"]))
    if form.get("zero_tax_note"):
        story.append(Paragraph(form["zero_tax_note"], s["small"]))
    for w in form.get("warnings") or []:
        story.append(Paragraph("※ " + w, s["small"]))
    story.append(Paragraph("e-Tax（国税電子申告・納税システム）: https://www.e-tax.nta.go.jp/ ・ 源泉所得税の納付: 国税庁ホームページ「源泉所得税の納付」", s["small"]))
    return _build(story, f"{form['form']} 転記用 {form['period_start'][:7]}")
