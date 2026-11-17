"""
報酬支払明細の PDF（M6）。書き方は accounting/pdf.py に合わせる（reportlab、CID フォント HeiseiKakuGo-W5、A4）。
数字は reports.person_report の結果をそのまま並べるだけ（ここで計算しない）。
"""
from __future__ import annotations

from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, Spacer

from apps.accounting.pdf import _build, _minutes, _styles, _table, yen
from apps.core import terms
from apps.core.money import D


def _count(v) -> str:
    n = D(v or 0)
    return f"{n.normalize():f}" if n else "--"


def pay_statement_pdf(report: dict, labels: dict[str, str]) -> bytes:
    """report: reports.person_report の戻り値。labels: 店舗の名称変更（core.terms.term_labels）。"""
    s = _styles()
    tx = lambda text: terms.relabel(text, labels)  # noqa: E731
    t = report["totals"]
    emp = report["employee"]
    cast = emp["role"] == "cast"
    withholding = bool(report["settings"]["withholding_enabled"])
    period = f"{report['from'].replace('-', '/')} 〜 {report['to'].replace('-', '/')}"
    story = [
        Paragraph(f"報酬支払明細書　{emp['name']} 様", s["title"]),
        Paragraph(f"{report['store_name']}　{period}", s["body"]),
        Spacer(1, 3 * mm),
    ]

    work = [["勤務", ""], ["労働日数", f"{_count(t['days'])} 日"], ["労働時間", _minutes(t["minutes"])],
            ["平均時給", yen(t["avg_hourly"]) if t.get("avg_hourly") else "--"]]
    story.append(_table(work, col_widths=[60 * mm, 40 * mm], number_cols=[1]))
    story.append(Spacer(1, 3 * mm))

    pay = [["支給対象", "金額"], ["時間報酬", yen(t["wage"])]]
    if cast:
        pay += [[tx(label), yen(t[key])] for label, key in (
            ("呼び小計バック", "back_yobi_subtotal"), ("同伴小計バック", "back_douhan_subtotal"), ("本指名バック", "back_nomination"),
            ("場内指名バック", "back_jounai"), ("同伴バック", "back_douhan"), ("ドリンクバック", "back_drink"), ("ボトルバック", "back_bottle"),
            ("フードバック", "back_food"))]
    pay += [["その他バック", yen(t["back_other"])], ["ボーナス（調整・正）", yen(t["bonus"])], ["マイナス（調整・負）", "−" + yen(t["minus"]) if D(t["minus"]) else yen(0)],
            ["総支給額", yen(t["gross"])]]
    story.append(_table(pay, col_widths=[60 * mm, 40 * mm], number_cols=[1]))
    story.append(Spacer(1, 3 * mm))

    ded = [["控除・支払い", "金額"], ["控除（厚生費など）", yen(t["deducted"])]]
    if withholding:
        ded.append(["源泉所得税", yen(t["withheld"])])
    ded += [["日払い（渡した額）", yen(t["paid"])], ["残り支給額", yen(t["balance"])]]
    story.append(_table(ded, col_widths=[60 * mm, 40 * mm], number_cols=[1]))
    story.append(Spacer(1, 4 * mm))

    story.append(Paragraph("日別", s["h2"]))
    head = ["営業日", "勤務時間", "時間報酬", "バック計", "総支給額", "控除"] + (["源泉所得税"] if withholding else []) + ["日払い", "残り"]
    days = [head]
    for r in report["days"]:
        row = [r["date"][5:].replace("-", "/"), _minutes(r["minutes"]), yen(r["wage"]), yen(r["back_total"]), yen(r["gross"]), yen(r["deducted"])]
        if withholding:
            row.append(yen(r["withheld"]))
        days.append(row + [yen(r["paid"]), yen(r["balance"])])
    total = ["月の合計", _minutes(t["minutes"]), yen(t["wage"]), yen(t["back_total"]), yen(t["gross"]), yen(t["deducted"])]
    if withholding:
        total.append(yen(t["withheld"]))
    days.append(total + [yen(t["paid"]), yen(t["balance"])])
    n = len(head)
    story.append(_table(days, col_widths=[22 * mm, 24 * mm] + [(182 - 46) / (n - 2) * mm] * (n - 2), number_cols=range(1, n)))
    story.append(Spacer(1, 3 * mm))
    story.append(Paragraph(
        "総支給額 = 時間報酬 ＋ バック計（調整を含む）。残り支給額 = 総支給額 − 控除 − " + ("源泉所得税 − " if withholding else "") + "日払い。"
        "月の合計は日別の合計と一致します。金額は勤怠・バック・日払いの記録をそのまま集めたものです。", s["small"]))
    if withholding:
        story.append(Paragraph("源泉所得税は、日払いのお渡しのときに計算・記録した額です。お店が預かって税務署に納付します。", s["small"]))
    return _build(story, f"報酬支払明細書 {emp['name']} {report['month']}")
