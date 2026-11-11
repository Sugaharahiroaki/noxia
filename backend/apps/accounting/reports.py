"""
法定帳票の作成支援データ（所得税徴収高計算書への転記用）とキャスト別明細。

確認した様式（国税庁「納付書の記載のしかた」2026-11 時点）:
- 報酬・料金等の所得税徴収高計算書: 「ホステス等の報酬・料金」は区分コード 08。人員は実人員、支払額は総額。
  施行令322条の控除額は「摘要」欄に「支払額からの控除額」として合計を書く。納期等の区分は支払った年月。
- 給与所得・退職所得等の所得税徴収高計算書: 俸給・給料等／賞与／日雇労務者の賃金（延べ人員）など。納期の特例は期間の最初と最後の年月。
- 「年度」欄は納付する日の属する国の会計年度（4月〜翌3月）。「納期等の区分」は 元号(令和=5)＋年2桁＋月2桁。

ここで作るのは NOXIA 独自の転記用データで、e-Tax に取り込める形式ではない（正式な取込形式は確認できていない）。
"""
from __future__ import annotations

from datetime import date

from . import jp_holidays
from .models import SalaryKind, TaxCategory, TaxPayer, WithholdingRecord
from .services import hostess_totals, month_period, salary_totals

OFFICIAL_NOTICE = "e-Tax・納付書への転記用です（NOXIA 独自の形式。e-Tax に取り込める形式ではありません）。内容を確認し、経営者ご本人が e-Tax で送信・納付してください。"


def _taxpayer_info(tp: TaxPayer) -> dict:
    return {
        "name": tp.name, "entity_type": tp.get_entity_type_display(), "address": tp.address, "phone": tp.phone,
        "corporate_number": tp.corporate_number, "representative_name": tp.representative_name,
        "tax_office_name": tp.tax_office_name, "reference_number": tp.reference_number,
    }


def _missing_info(tp: TaxPayer) -> list[str]:
    w = []
    if not tp.tax_office_name:
        w.append("所轄税務署が未設定です。")
    if not tp.reference_number:
        w.append("源泉所得税の整理番号が未設定です（税務署から通知された番号）。")
    if not tp.address:
        w.append("所在地（納税地）が未設定です。")
    return w


def hostess_form(tp: TaxPayer, any_day: date) -> dict:
    start, end, due = month_period(tp, TaxCategory.HOSTESS_FEE, any_day)
    t = hostess_totals(tp, start, end)["totals"]
    warnings = _missing_info(tp)
    if t["needs_review_count"]:
        warnings.append(f"要確認の支払いが {t['needs_review_count']} 件あり、集計に含めていません。確認してから転記してください。")
    return {
        "form": "報酬・料金等の所得税徴収高計算書",
        "notice": OFFICIAL_NOTICE,
        "taxpayer": _taxpayer_info(tp),
        "nendo": jp_holidays.fiscal_year_code(due),
        "nendo_note": "納付する日の属する会計年度（4月〜翌3月）。納期限の日に納付する場合の値です。",
        "nouki": {"label": jp_holidays.reiwa_label(start.year, start.month), "code": jp_holidays.nouki_code(start.year, start.month)},
        "rows": [{"code": "08", "label": "ホステス等の報酬・料金", "people": t["people"], "payment_amount": str(t["target_total"]), "tax": str(t["tax_total"])}],
        "tekiyo": [{"label": "支払額からの控除額", "amount": str(t["deduction_total"])}],
        "penalty_note": "延滞税・不納付加算税の欄は、納期限後に納付する場合に税務署の案内に従って記入してください（NOXIA では計算しません）。",
        "total": str(t["tax_total"]),
        "due_date": due.isoformat(),
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
        "detail": {"gross_total": str(t["gross_total"]), "withheld_total": str(t["withheld_total"]), "adjustment_total": str(t["adjustment_total"]),
                   "payment_count": t["payment_count"]},
        "warnings": warnings,
    }


def salary_form(tp: TaxPayer, any_day: date) -> dict:
    start, end, due = month_period(tp, TaxCategory.SALARY, any_day)
    data = salary_totals(tp, start, end)
    special = tp.payroll_due_special
    nouki = {"label": jp_holidays.reiwa_label(start.year, start.month), "code": jp_holidays.nouki_code(start.year, start.month)}
    if special:
        nouki = {"label": f"自 {jp_holidays.reiwa_label(start.year, start.month)} 至 {jp_holidays.reiwa_label(end.year, end.month)}",
                 "code": f"自{jp_holidays.nouki_code(start.year, start.month)} 至{jp_holidays.nouki_code(end.year, end.month)}"}
    labels = {SalaryKind.REGULAR: "俸給、給料等", SalaryKind.BONUS: "賞与（役員賞与を除く）", SalaryKind.DAILY_WORKER: "日雇労務者の賃金"}
    rows = [{"kind": r["kind"], "label": labels[r["kind"]], "people": r["people"], "payment_amount": str(r["gross"]), "tax": str(r["withholding"]),
             "people_note": "延べ人員" if r["kind"] == SalaryKind.DAILY_WORKER else "実人員"} for r in data["rows"]]
    warnings = _missing_info(tp)
    t = data["totals"]
    if t["noxia_salary_count"] and not t["payment_count"]:
        warnings.append("NOXIA で給与の支払いがありますが、給与計算ソフトで計算した源泉徴収額が入力されていません。")
    return {
        "form": "給与所得・退職所得等の所得税徴収高計算書" + ("（納期特例分）" if special else "（一般分）"),
        "notice": OFFICIAL_NOTICE + " 給与の源泉徴収額は給与計算ソフトで計算した値を入力したものです。",
        "taxpayer": _taxpayer_info(tp),
        "nendo": jp_holidays.fiscal_year_code(due),
        "nendo_note": "納付する日の属する会計年度（4月〜翌3月）。納期限の日に納付する場合の値です。",
        "nouki": nouki,
        "rows": rows,
        "tekiyo": [],
        "penalty_note": "延滞税・不納付加算税の欄は、納期限後に納付する場合に税務署の案内に従って記入してください。",
        "total": str(t["tax_total"]),
        "due_date": due.isoformat(),
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
        "zero_tax_note": "納付する税額がない場合も徴収高計算書の提出が必要です（e-Tax で送信できます）。",
        "warnings": warnings,
    }


CAST_DETAIL_HEADER = [
    "支払日", "店舗", "キャスト（表示名）", "氏名", "税務区分", "状態", "勤務日", "計算期間（開始）", "計算期間（終了）", "日数",
    "報酬総額", "源泉徴収対象額", "控除額", "課税対象額", "税率(%)", "源泉所得税", "その他控除", "差引支給額", "計算ルール", "計算エンジン", "取消",
]


def cast_detail_rows(tp: TaxPayer, start: date, end: date) -> list[list]:
    rows = []
    for r in WithholdingRecord.objects.filter(taxpayer=tp, paid_on__gte=start, paid_on__lte=end).select_related("store", "employee").order_by("paid_on", "id"):
        rows.append([
            r.paid_on.isoformat(), r.store.name, r.employee.display_name, r.employee.full_name, r.get_tax_category_display(), r.get_status_display(),
            r.work_date.isoformat() if r.work_date else "", r.period_start.isoformat() if r.period_start else "",
            r.period_end.isoformat() if r.period_end else "", r.period_days, r.gross_amount, r.target_amount, r.deduction_applied, r.taxable_amount,
            r.rate_percent, r.withholding_amount, r.other_deduction_amount, r.net_amount, r.rule_version, r.engine_version,
            f"取消: {r.void_reason}" if r.is_voided else "",
        ])
    return rows
