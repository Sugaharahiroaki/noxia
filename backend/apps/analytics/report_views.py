"""管理用の集計 API（読み取り専用）。集計の中身は reports.py。"""
from __future__ import annotations

from datetime import date

from django.utils.dateparse import parse_date
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.business_date import current_business_date
from apps.core.exceptions import ApiError
from apps.core.permissions import (
    CAP_CUSTOMERS_VIEW, CAP_DASHBOARD, CAP_EXPORT_PAYROLL, CAP_EXPORT_SALES, CAP_PAYROLL_VIEW_ALL, CAP_PAYROLL_VIEW_SELF, CAP_SALES_VIEW, StoreCaps, is_reauthenticated,
)

from . import reports

MAX_RANGE_DAYS = 400


def parse_range(request) -> tuple[date, date]:
    """from / to（営業日）。省略時は当月 1 日〜今の営業日。"""
    today = current_business_date(request.store)
    f = parse_date(request.query_params.get("from") or "") or today.replace(day=1)
    t = parse_date(request.query_params.get("to") or "") or today
    if t < f:
        f, t = t, f
    if (t - f).days > MAX_RANGE_DAYS:
        raise ApiError("validation_error", f"期間は{MAX_RANGE_DAYS}日以内で指定してください。")
    return f, t


def flag(request, name: str) -> bool:
    return request.query_params.get(name) in ("1", "true", "True")


class SlipListView(APIView):
    """伝票一覧（期間、支払方法、値引きあり、取消済みで絞り込み）。"""

    def get_permissions(self):
        return [StoreCaps(CAP_SALES_VIEW)]

    def get(self, request):
        f, t = parse_range(request)
        return Response(reports.slip_list(request.store, f, t, payment=request.query_params.get("payment") or "",
                                          discount_only=flag(request, "discount"), cancelled=flag(request, "cancelled")))


class SlipDetailView(APIView):
    """伝票詳細（明細、会計、キャスト別の売上小計）。別店舗の伝票は 404。"""

    def get_permissions(self):
        return [StoreCaps(CAP_SALES_VIEW)]

    def get(self, request, visit_id):
        data = reports.slip_detail(request.store, visit_id)
        if data is None:
            raise ApiError("not_found", "伝票が見つかりません。", status=404)
        return Response(data)


def parse_month(request) -> tuple[int, int]:
    """month=YYYY-MM。省略時は今の営業日の月。"""
    raw = request.query_params.get("month") or ""
    today = current_business_date(request.store)
    if not raw:
        return today.year, today.month
    try:
        y, m = raw.split("-")
        y, m = int(y), int(m)
        date(y, m, 1)
    except (ValueError, TypeError):
        raise ApiError("validation_error", "月は YYYY-MM の形式で指定してください。")
    return y, m


class MonthlyReportView(APIView):
    """収支（月次）。1 行 = 1 営業日。"""

    def get_permissions(self):
        return [StoreCaps(CAP_DASHBOARD)]

    def get(self, request):
        y, m = parse_month(request)
        return Response(reports.monthly_report(request.store, y, m))


class DailyReportView(APIView):
    """収支（日次）＝営業日報。"""

    def get_permissions(self):
        return [StoreCaps(CAP_DASHBOARD)]

    def get(self, request):
        d = parse_date(request.query_params.get("date") or "") or current_business_date(request.store)
        return Response(reports.daily_report(request.store, d))


class CashMonthView(APIView):
    """レジ精算の月間一覧。"""

    def get_permissions(self):
        return [StoreCaps(CAP_DASHBOARD)]

    def get(self, request):
        y, m = parse_month(request)
        f, t = reports.month_range(y, m)
        rows = reports.cash_days(request.store, f, t)
        from apps.core.money import D, ZERO

        return Response({
            "month": f"{y}-{m:02d}", "rows": rows,
            "totals": {
                "cash_sales": str(sum((D(r["cash_sales"]) for r in rows), ZERO)),
                "cash_in": str(sum((D(r["cash_in"]) for r in rows), ZERO)),
                "cash_out": str(sum((D(r["cash_out"]) for r in rows), ZERO)),
                "difference": str(sum((D(r["difference"]) for r in rows if r["difference"] is not None), ZERO)),
                "set_aside_amount": str(sum((D(r["set_aside_amount"]) for r in rows if r["set_aside_amount"] is not None), ZERO)),
            },
            "formula_note": reports.CASH_FORMULA_NOTE,
        })

# --- M3: キャスト・スタッフ集計（報酬） -----------------------------------------
class PeopleReportView(APIView):
    """キャスト／スタッフの月次の集計表（全員分）。既存の支払集計と同じく payroll.view_all と再認証が必要。"""

    cast = True

    def get_permissions(self):
        return [StoreCaps(CAP_PAYROLL_VIEW_ALL, reauth=True)]

    def get(self, request):
        y, m = parse_month(request)
        return Response(reports.people_report(request.store, y, m, cast=self.cast))


class StaffReportView(PeopleReportView):
    cast = False


class PersonReportView(APIView):
    """
    1 人の報酬支払明細と日別表。
    全員分を見られる人（payroll.view_all）は再認証のうえで誰の分でも。キャスト本人（payroll.view_self）は自分の分だけ。
    """

    def get_permissions(self):
        return [StoreCaps()]

    def get(self, request, employee_id):
        emp = person_for_request(request, employee_id)
        y, m = parse_month(request)
        return Response(reports.person_report(request.store, emp, y, m))


def person_for_request(request, employee_id):
    """報酬支払明細を見てよい相手かを確かめて、従業員を返す（画面と PDF で同じ判定）。"""
    from apps.staff.models import Employee

    access = request.store_access
    own = getattr(request.user, "employee", None)
    is_own = own is not None and own.id == int(employee_id) and own.company_id == request.store.company_id
    if CAP_PAYROLL_VIEW_ALL in access.caps:
        if not is_own and not is_reauthenticated(request):
            raise ApiError("reauth_required", "この操作にはパスワードの再確認が必要です。", status=403)
    elif not (is_own and CAP_PAYROLL_VIEW_SELF in access.caps):
        raise ApiError("forbidden", "この操作を行う権限がありません。", status=403)
    emp = Employee.objects.filter(id=employee_id, company=request.store.company).first()
    if emp is None:
        raise ApiError("not_found", "従業員が見つかりません。", status=404)
    return emp


class PersonPdfView(APIView):
    """報酬支払明細の PDF。見られる人は画面（PersonReportView）と同じ。"""

    def get_permissions(self):
        return [StoreCaps()]

    def get(self, request, employee_id):
        from apps.accounting.exports import file_response
        from apps.core import terms

        from .report_pdf import pay_statement_pdf

        emp = person_for_request(request, employee_id)
        y, m = parse_month(request)
        data = reports.person_report(request.store, emp, y, m)
        pdf = pay_statement_pdf(data, terms.term_labels(request.store))
        return file_response(request, f"pay_statement_{request.store.code}_{emp.id}_{data['month']}.pdf", pdf, "application/pdf", "export.report.pay_statement")


CAST_CSV_COLUMNS = [
    ("名前", "employee_name"), ("総支給額", "gross"), ("残り支給額", "balance"), ("オーダー小計", "order_subtotal"),
    ("呼び本計", "yobi_total"), ("呼び小計", "yobi_subtotal"), ("同伴本計", "douhan_total"), ("同伴小計", "douhan_subtotal"), ("給率(%)", "pay_ratio"),
    ("勤務日数", "days"), ("勤務分", "minutes"), ("平均時給", "avg_hourly"), ("時間報酬", "wage"),
    ("本指名 組数", "nomination_groups"), ("本指名 回数", "nomination_count"), ("本指名バック", "back_nomination"),
    ("場内 組数", "jounai_groups"), ("場内 回数", "jounai_count"), ("場内バック", "back_jounai"),
    ("同伴 組数", "douhan_groups"), ("同伴 回数", "douhan_count"), ("同伴バック", "back_douhan"),
    ("呼び小計バック", "back_yobi_subtotal"), ("同伴小計バック", "back_douhan_subtotal"),
    ("ドリンク売上", "sales_drink"), ("ドリンク本数", "qty_drink"), ("ドリンクバック", "back_drink"),
    ("ボトル売上", "sales_bottle"), ("ボトル本数", "qty_bottle"), ("ボトルバック", "back_bottle"),
    ("フード売上", "sales_food"), ("フード回数", "qty_food"), ("フードバック", "back_food"),
    ("その他バック", "back_other"), ("バック計", "back_total"), ("ボーナス", "bonus"), ("マイナス", "minus"),
    ("控除", "deducted"), ("日払い", "paid"),
]


class PeopleCsvView(APIView):
    """キャスト（または スタッフ）集計の CSV。既存の支払 CSV と同じく export.payroll と再認証が必要。"""

    def get_permissions(self):
        return [StoreCaps(CAP_EXPORT_PAYROLL, reauth=True)]

    def get(self, request, kind):
        import csv
        import io

        from django.http import HttpResponse

        from apps.core.audit import audit

        if kind not in ("cast", "staff"):
            raise ApiError("not_found", "出力種別が不正です。", status=404)
        y, m = parse_month(request)
        data = reports.people_report(request.store, y, m, cast=(kind == "cast"))
        cols = list(CAST_CSV_COLUMNS)
        if data["settings"]["withholding_enabled"]:
            cols.append(("源泉所得税", "withheld"))  # 源泉徴収をしている店舗だけ出す
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow([c[0] for c in cols])
        for r in [{"employee_name": "合計", **data["totals"]}] + data["rows"]:
            w.writerow([r.get(c[1]) if r.get(c[1]) is not None else "" for c in cols])
        filename = f"{kind}_report_{request.store.code}_{data['month']}.csv"
        resp = HttpResponse("\ufeff" + buf.getvalue(), content_type="text/csv; charset=utf-8")
        resp["Content-Disposition"] = f'attachment; filename="{filename}"'
        audit(request, f"export.report.{kind}", changes={"filename": filename})
        return resp

# --- M4: タグ集計・商品集計 ---------------------------------------------------
def _csv(request, filename: str, header: list, rows: list, action: str):
    """CSV の応答（既存の exports と同じ作法: BOM つき UTF-8、操作履歴に残す）。"""
    import csv
    import io

    from django.http import HttpResponse

    from apps.core.audit import audit

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    for r in rows:
        w.writerow(r)
    resp = HttpResponse("\ufeff" + buf.getvalue(), content_type="text/csv; charset=utf-8")
    resp["Content-Disposition"] = f'attachment; filename="{filename}"'
    audit(request, action, changes={"filename": filename})
    return resp


class TagReportView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_SALES_VIEW)]

    def get(self, request):
        f, t = parse_range(request)
        group_by = request.query_params.get("group_by") or "tag"
        if group_by not in ("tag", "day", "month"):
            raise ApiError("validation_error", "集計の単位は tag / day / month のいずれかです。")
        return Response(reports.tag_report(request.store, f, t, group_by))


class ItemReportView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_SALES_VIEW)]

    def get(self, request):
        f, t = parse_range(request)
        return Response(reports.item_report(request.store, f, t))


class ReportCsvView(APIView):
    """タグ集計・商品集計の CSV（export.sales）。"""

    def get_permissions(self):
        return [StoreCaps(CAP_EXPORT_SALES)]

    def get(self, request, kind):
        f, t = parse_range(request)
        tag = f"{request.store.code}_{f.isoformat()}_{t.isoformat()}"
        if kind == "tags":
            group_by = request.query_params.get("group_by") or "tag"
            if group_by not in ("tag", "day", "month"):
                raise ApiError("validation_error", "集計の単位は tag / day / month のいずれかです。")
            d = reports.tag_report(request.store, f, t, group_by)
            rows = [[r["period"], r["tag_name"], r["count"], r["guests"], r["subtotal"], r["cash"], r["card"], r["receivable"], r["total"]] for r in d["rows"]]
            return _csv(request, f"tags_{tag}.csv", ["期間", "タグ", "件数", "客数", "伝票小計", "現金", "カード", "売掛", "販売額"], rows, "export.report.tags")
        if kind == "items":
            d = reports.item_report(request.store, f, t)
            rows = [["商品別", p["category_name"], p["name"], "", p["quantity"], p["amount"]] for p in d["products"]]
            for c in d["casts"]:
                rows += [["キャスト別", "", it["name"], c["employee_name"], it["quantity"], it["amount"]] for it in c["items"]]
            return _csv(request, f"items_{tag}.csv", ["区分", "カテゴリー", "商品", "キャスト", "個数", "金額"], rows, "export.report.items")
        if kind == "customers":
            if CAP_CUSTOMERS_VIEW not in request.store_access.caps:
                raise ApiError("forbidden", "この操作を行う権限がありません。", status=403)
            d = reports.customer_report(request.store, current_business_date(request.store))
            rows = [[r["name"], r["store_name"], r["rank"], r["keep_count"], r["visits"], r["visits_recent"], r["first_visit"] or "", r["last_visit"] or "",
                     r["avg_minutes"] if r["avg_minutes"] is not None else "", r["unit_price"] or "", r["total"], r["cash"], r["card"], r["receivable"],
                     r["nomination"], r["jounai"], r["douhan"], r["assigned_employee_name"]] for r in d["rows"]]
            return _csv(request, f"customers_{request.store.code}_{d['as_of']}.csv", CUSTOMER_CSV_HEADER, rows, "export.report.customers")
        raise ApiError("not_found", "出力種別が不正です。", status=404)


# --- M5: お客様分析 -----------------------------------------------------------
CUSTOMER_CSV_HEADER = ["お客様", "登録店舗", "ランク", "キープ", "来店回数", "直近3か月の来店", "初回来店日", "最終来店日", "平均滞在(分)", "単価",
                       "会計", "現金", "カード", "売掛", "本指名", "場内指名", "同伴", "担当"]


class CustomerReportView(APIView):
    """お客様分析。顧客情報（customers.view）と売上（sales.view）の両方が見られる人だけ。"""

    def get_permissions(self):
        return [StoreCaps(CAP_CUSTOMERS_VIEW, CAP_SALES_VIEW)]

    def get(self, request):
        return Response(reports.customer_report(request.store, current_business_date(request.store)))


# --- M6: 履歴・XLSX 出力 -------------------------------------------------------
class CashHistoryView(APIView):
    """入出金の履歴（月の合計と明細）。"""

    def get_permissions(self):
        return [StoreCaps(CAP_DASHBOARD)]

    def get(self, request):
        y, m = parse_month(request)
        return Response(reports.cash_history(request.store, y, m))


class DeletionHistoryView(APIView):
    """伝票の削除履歴（取消済みの伝票と、取消・返金の操作履歴）。"""

    def get_permissions(self):
        return [StoreCaps(CAP_SALES_VIEW)]

    def get(self, request):
        f, t = parse_range(request)
        return Response(reports.deletion_history(request.store, f, t))


class ReportXlsxView(APIView):
    """集計画面の表の XLSX 出力。必要な権限は種類ごと（report_tables.KINDS）。キャスト・スタッフ集計は再認証が必要。"""

    def get_permissions(self):
        from .report_tables import KINDS

        spec = KINDS.get(self.kwargs.get("kind"))
        if spec is None:
            return [StoreCaps()]
        caps, reauth, _period = spec
        return [StoreCaps(*caps, reauth=True if reauth else None)]

    def get(self, request, kind):
        from apps.accounting.exports import xlsx_response

        from .report_tables import KINDS, build

        if kind not in KINDS:
            raise ApiError("not_found", "出力種別が不正です。", status=404)
        period = KINDS[kind][2]
        kwargs = {"today": current_business_date(request.store)}
        if period == "range":
            kwargs["f"], kwargs["t"] = parse_range(request)
        elif period == "month":
            kwargs["year"], kwargs["month"] = parse_month(request)
        if kind == "tags":
            group_by = request.query_params.get("group_by") or "tag"
            if group_by not in ("tag", "day", "month"):
                raise ApiError("validation_error", "集計の単位は tag / day / month のいずれかです。")
            kwargs["group_by"] = group_by
        base, sheets = build(kind, request.store, **kwargs)
        return xlsx_response(request, f"{base}.xlsx", sheets, f"export.report.{kind}.xlsx")
