"""
経営管理の集計と CSV 出力。

- 売上は会計(Checkout, confirmed)の請求額を基準にし、現金・カード・未収の内訳は支払いから集計する。
- 従業員別売上は「担当（primary_employee）」の伝票の会計額で帰属させる（店舗全体の売上と重複集計しない）。
  指名・同伴の明細は対象従業員に帰属する補助指標として別に出す。
- 仕入額（発注の入荷金額）と売上原価（販売数量 × 原価）は区別する。原価未設定の商品があれば利益は概算または算出不可とする。
"""
from __future__ import annotations

import csv
import io
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Count, F, Q, Sum
from django.http import HttpResponse
from django.utils.dateparse import parse_date
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.cash.models import BusinessDay, CashMovement
from apps.core.audit import audit
from apps.core.business_date import business_date_now, current_business_date
from apps.core.exceptions import ApiError
from apps.core.models import Company, OperatorAccessLog, Store
from apps.core.money import D, ZERO
from apps.core.permissions import (
    CAP_DASHBOARD, CAP_EXPORT_ATTENDANCE, CAP_EXPORT_BACKS, CAP_EXPORT_CASH, CAP_EXPORT_INVENTORY, CAP_EXPORT_PAYROLL, CAP_EXPORT_SALES,
    IsAuthenticatedUser, StoreCaps, company_role_for, is_reauthenticated, resolve_store_access,
)
from apps.inventory.models import GoodsReceiptLine, PurchaseOrder, StockMovement
from apps.sales.models import Checkout, OrderLine, Payment, Refund, Visit
from apps.staff.models import BackEntry, EmployeePayment, Shift
from apps.staff.payroll import shift_minutes, shift_wage


def _range(request) -> tuple[date, date]:
    today = current_business_date(request.store) if hasattr(request, "store") else date.today()
    f = parse_date(request.query_params.get("from") or "") or today.replace(day=1)
    t = parse_date(request.query_params.get("to") or "") or today
    if t < f:
        f, t = t, f
    if (t - f).days > 400:
        raise ApiError("validation_error", "期間は400日以内で指定してください。")
    return f, t


def sales_summary(store_ids, f: date, t: date) -> dict:
    checkouts = Checkout.objects.filter(store_id__in=store_ids, business_date__gte=f, business_date__lte=t, status="confirmed")
    payments = Payment.objects.filter(store_id__in=store_ids, business_date__gte=f, business_date__lte=t, checkout__status="confirmed", status="valid")
    pay = {r["method"]: r["s"] or ZERO for r in payments.values("method").annotate(s=Sum("amount"))}
    refunds = Refund.objects.filter(store_id__in=store_ids, business_date__gte=f, business_date__lte=t).aggregate(s=Sum("amount"))["s"] or ZERO
    visits = Visit.objects.filter(store_id__in=store_ids, business_date__gte=f, business_date__lte=t).exclude(status="cancelled")
    guests = visits.aggregate(s=Sum("guest_count"))["s"] or 0
    total = checkouts.aggregate(s=Sum("total"))["s"] or ZERO
    return {
        "total": str(total), "checkout_count": checkouts.count(), "visit_count": visits.count(), "guest_count": guests,
        "avg_per_guest": str((total / guests).quantize(Decimal("1"))) if guests else None,
        "cash": str(pay.get("cash", ZERO)), "card": str(pay.get("card", ZERO)), "receivable": str(pay.get("receivable", ZERO)), "refunds": str(refunds),
    }


class DailySalesView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_DASHBOARD)]

    def get(self, request):
        f, t = _range(request)
        store = request.store
        rows = []
        checkouts = Checkout.objects.filter(store=store, business_date__gte=f, business_date__lte=t, status="confirmed").values("business_date").annotate(total=Sum("total"), n=Count("id"))
        by_date = {r["business_date"]: r for r in checkouts}
        pays = Payment.objects.filter(store=store, business_date__gte=f, business_date__lte=t, checkout__status="confirmed", status="valid").values("business_date", "method").annotate(s=Sum("amount"))
        pay_map: dict = {}
        for r in pays:
            pay_map.setdefault(r["business_date"], {})[r["method"]] = r["s"]
        guests = {r["business_date"]: r["g"] for r in Visit.objects.filter(store=store, business_date__gte=f, business_date__lte=t).exclude(status="cancelled").values("business_date").annotate(g=Sum("guest_count"))}
        d = f
        while d <= t:
            c = by_date.get(d)
            p = pay_map.get(d, {})
            total = c["total"] if c else ZERO
            g = guests.get(d, 0) or 0
            rows.append({"date": d.isoformat(), "total": str(total), "checkouts": c["n"] if c else 0, "guests": g,
                         "avg_per_guest": str((total / g).quantize(Decimal("1"))) if g else None,
                         "cash": str(p.get("cash", ZERO)), "card": str(p.get("card", ZERO)), "receivable": str(p.get("receivable", ZERO))})
            d += timedelta(days=1)
        return Response({"from": f, "to": t, "rows": rows, "summary": sales_summary([store.id], f, t)})


class MonthlySalesView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_DASHBOARD)]

    def get(self, request):
        store = request.store
        today = current_business_date(store)
        months = int(request.query_params.get("months") or 12)
        rows = []
        y, m = today.year, today.month
        for _ in range(months):
            first = date(y, m, 1)
            last = (date(y + (m // 12), (m % 12) + 1, 1) - timedelta(days=1))
            s = sales_summary([store.id], first, last)
            rows.append({"month": f"{y}-{m:02d}", **s})
            m -= 1
            if m == 0:
                m = 12
                y -= 1
        rows.reverse()
        return Response(rows)


class BreakdownView(APIView):
    """コース別・商品別・キャスト別売上とバック、勤務時間と人件費の目安、仕入・経費、利益の目安。"""

    def get_permissions(self):
        return [StoreCaps(CAP_DASHBOARD)]

    def get(self, request):
        f, t = _range(request)
        store = request.store
        lines = OrderLine.objects.filter(store=store, status="active", checkout__status="confirmed", checkout__business_date__gte=f, checkout__business_date__lte=t)
        by_type = {r["line_type"]: r for r in lines.values("line_type").annotate(s=Sum("amount"), n=Count("id"))}
        by_course = [{"course": r["course_entry__course_name"], "amount": str(r["s"]), "count": r["n"]}
                     for r in lines.filter(line_type__in=["course_set", "extension", "course_change"]).values("course_entry__course_name").annotate(s=Sum("amount"), n=Count("id")).order_by("-s")]
        by_product = [{"product_id": r["product_id"], "name": r["name"], "amount": str(r["s"]), "quantity": str(r["q"])}
                      for r in lines.filter(line_type="product").values("product_id", "name").annotate(s=Sum("amount"), q=Sum("quantity")).order_by("-s")[:50]]
        # キャスト別: 担当伝票の会計額（重複なし）+ 指名・同伴件数 + 確定バック
        checkouts = Checkout.objects.filter(store=store, business_date__gte=f, business_date__lte=t, status="confirmed")
        by_emp = {r["visit__primary_employee_id"]: r for r in checkouts.values("visit__primary_employee_id", "visit__primary_employee__display_name").annotate(s=Sum("total"), n=Count("id"))}
        backs = {r["employee_id"]: r["s"] for r in BackEntry.objects.filter(store=store, business_date__gte=f, business_date__lte=t, status="confirmed").values("employee_id").annotate(s=Sum("amount"))}
        fee_counts: dict = {}
        for l in lines.filter(line_type__in=["nomination", "douhan"]).prefetch_related("back_targets"):
            for bt in l.back_targets.all():
                fee_counts.setdefault(bt.employee_id, {"nomination": 0, "douhan": 0})
                fee_counts[bt.employee_id][l.line_type] += int(l.quantity)
        emp_ids = set(by_emp) | set(backs) | set(fee_counts)
        from apps.staff.models import Employee

        names = {e.id: e.display_name for e in Employee.objects.filter(id__in=[i for i in emp_ids if i])}
        by_cast = []
        for eid in emp_ids:
            r = by_emp.get(eid)
            by_cast.append({
                "employee_id": eid, "employee_name": names.get(eid, "担当なし") if eid else "担当なし",
                "attributed_sales": str(r["s"] if r else ZERO), "checkouts": r["n"] if r else 0,
                "nominations": fee_counts.get(eid, {}).get("nomination", 0), "douhan": fee_counts.get(eid, {}).get("douhan", 0),
                "back_total": str(backs.get(eid, ZERO)),
            })
        by_cast.sort(key=lambda x: D(x["attributed_sales"]), reverse=True)
        # 人件費の目安
        shifts = Shift.objects.filter(store=store, business_date__gte=f, business_date__lte=t).select_related("employee").prefetch_related("breaks")
        minutes = 0
        wage_total = ZERO
        missing_wage = 0
        for s in shifts:
            mnt = shift_minutes(s)
            minutes += mnt
            w, amt = shift_wage(s, mnt)
            if w is None:
                missing_wage += 1
            wage_total += amt
        back_total = sum(backs.values(), ZERO)
        labor_estimate = wage_total + back_total
        # 仕入（入荷金額）と経費
        receipts = GoodsReceiptLine.objects.filter(receipt__store=store, receipt__received_at__date__gte=f, receipt__received_at__date__lte=t).select_related("po_line")
        purchases = sum(((l.bottles / (l.po_line.units_per_order_unit or 1)) * l.po_line.unit_price for l in receipts), ZERO)
        expenses = CashMovement.objects.filter(store=store, business_day__date__gte=f, business_day__date__lte=t, kind="expense", is_voided=False).aggregate(s=Sum("amount"))["s"] or ZERO
        # 売上原価（販売数量×原価）: 原価未設定があれば概算
        cogs = ZERO
        cogs_missing = 0
        for l in lines.filter(line_type="product").select_related("product"):
            if l.product and l.product.cost_price is not None:
                if l.product.stock_mode == "shot" and l.product.volume_ml and l.product.shot_ml:
                    cogs += (D(l.product.cost_price) * D(l.product.shot_ml) / D(l.product.volume_ml) * l.quantity)
                else:
                    cogs += D(l.product.cost_price) * l.quantity
            else:
                cogs_missing += 1
        summary = sales_summary([store.id], f, t)
        total = D(summary["total"])
        profit_status = "estimate"
        if cogs_missing and cogs == 0:
            profit_status = "unavailable"
        elif cogs_missing or missing_wage:
            profit_status = "estimate_partial"
        gross_profit = total - cogs.quantize(Decimal("1"))
        operating = gross_profit - labor_estimate - expenses
        return Response({
            "from": f, "to": t, "summary": summary,
            "by_line_type": {k: {"amount": str(v["s"]), "count": v["n"]} for k, v in by_type.items()},
            "by_course": by_course, "by_product": by_product, "by_cast": by_cast,
            "labor": {"worked_minutes": minutes, "wage_estimate": str(wage_total), "back_total": str(back_total), "labor_estimate": str(labor_estimate),
                      "shifts_missing_wage": missing_wage, "notice": "時給×勤務時間＋確定バックの目安です。法定控除・割増賃金は含みません。"},
            "purchases": {"received_amount": str(purchases.quantize(Decimal("1"))), "cash_expenses": str(expenses),
                          "notice": "仕入額（入荷金額）であり、売上原価とは異なります。"},
            "cogs": {"amount": str(cogs.quantize(Decimal("1"))), "lines_missing_cost": cogs_missing},
            "profit": {"status": profit_status, "gross_profit": str(gross_profit) if profit_status != "unavailable" else None,
                       "operating_estimate": str(operating) if profit_status != "unavailable" else None,
                       "notice": {"estimate": "概算値です。", "estimate_partial": "原価または時給が未設定の項目があるため概算です。",
                                  "unavailable": "原価が未設定のため算出できません。"}[profit_status]},
        })


class CompanySummaryView(APIView):
    """自社内の店舗別集計と全店集計（許可された店舗だけ）。"""

    permission_classes = [IsAuthenticatedUser]

    def get(self, request, company_id):
        company = Company.objects.filter(id=company_id).first()
        if company is None:
            raise ApiError("forbidden", "この会社のデータを参照する権限がありません。", status=403)
        role = company_role_for(request.user, company)
        if role == "operator":
            OperatorAccessLog.objects.create(user=request.user, company=company, method=request.method, path=request.path[:300])
        today = date.today()
        f = parse_date(request.query_params.get("from") or "") or today.replace(day=1)
        t = parse_date(request.query_params.get("to") or "") or today
        allowed = []
        for store in Store.objects.filter(company=company):
            try:
                access = resolve_store_access(request.user, store.id)
            except ApiError:
                continue
            if CAP_DASHBOARD in access.caps:
                allowed.append(store)
        if not allowed:
            raise ApiError("forbidden", "この会社で集計を参照できる店舗がありません。", status=403)
        rows = [{"store_id": s.id, "store_name": s.name, "is_active": s.is_active, **sales_summary([s.id], f, t)} for s in allowed]
        return Response({"company": {"id": company.id, "name": company.name}, "from": f, "to": t, "stores": rows,
                         "total": sales_summary([s.id for s in allowed], f, t), "store_count": len(allowed)})


# --- CSV ----------------------------------------------------------------------
def _csv_response(request, filename: str, header: list[str], rows, action: str) -> HttpResponse:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    for r in rows:
        w.writerow(r)
    resp = HttpResponse("﻿" + buf.getvalue(), content_type="text/csv; charset=utf-8")
    resp["Content-Disposition"] = f'attachment; filename="{filename}"'
    audit(request, action, changes={"filename": filename})
    return resp


class ExportView(APIView):
    CAPS = {
        "sales": CAP_EXPORT_SALES, "payments": CAP_EXPORT_SALES, "cash": CAP_EXPORT_CASH, "backs": CAP_EXPORT_BACKS,
        "attendance": CAP_EXPORT_ATTENDANCE, "payroll": CAP_EXPORT_PAYROLL, "inventory": CAP_EXPORT_INVENTORY, "purchases": CAP_EXPORT_INVENTORY,
    }

    def get_permissions(self):
        kind = self.kwargs.get("kind")
        cap = self.CAPS.get(kind)
        if cap is None:
            return [StoreCaps(CAP_DASHBOARD)]
        return [StoreCaps(cap, reauth=(kind == "payroll"))]

    def get(self, request, kind):
        f, t = _range(request)
        store = request.store
        tag = f"{store.code}_{f.isoformat()}_{t.isoformat()}"
        if kind == "sales":
            qs = Checkout.objects.filter(store=store, business_date__gte=f, business_date__lte=t).select_related("visit__table").order_by("confirmed_at")
            rows = [[c.business_date, c.number, c.visit.slip_number, c.visit.table.name, c.kind, c.status, c.subtotal, c.discount_total, c.service_charge,
                     c.tax_total, c.rounding_adjustment, c.total, c.total_excl_tax, c.confirmed_at.isoformat(), c.void_reason] for c in qs]
            return _csv_response(request, f"sales_{tag}.csv", ["営業日", "会計番号", "伝票番号", "卓", "種別", "状態", "明細合計", "値引き", "サービス料", "税額", "端数調整", "請求額", "税抜金額", "確定日時", "取消理由"], rows, "export.sales")
        if kind == "payments":
            qs = Payment.objects.filter(store=store, business_date__gte=f, business_date__lte=t).select_related("checkout").order_by("paid_at")
            rows = [[p.business_date, p.checkout.number, p.method, p.amount, p.tendered, p.change_amount, p.status, p.paid_at.isoformat()] for p in qs]
            return _csv_response(request, f"payments_{tag}.csv", ["営業日", "会計番号", "方法", "充当額", "預り額", "釣銭", "状態", "日時"], rows, "export.payments")
        if kind == "cash":
            days = BusinessDay.objects.filter(store=store, date__gte=f, date__lte=t).order_by("date")
            rows = [[d.date, d.status, d.opening_float, d.cash_sales, d.card_sales, d.receivable_sales, d.cash_in_other, d.cash_out_expense, d.cash_out_daily_pay,
                     d.cash_out_other, d.cash_out_mid_collection, d.cash_refunds, d.theoretical_cash, d.counted_cash, d.difference, d.difference_reason,
                     d.next_day_float, d.set_aside_amount, d.shortage_amount] for d in days]
            moves = CashMovement.objects.filter(store=store, business_day__date__gte=f, business_day__date__lte=t).select_related("business_day", "employee").order_by("occurred_at")
            rows.append([])
            rows.append(["--- 入出金 ---"])
            rows.append(["営業日", "種別", "金額", "理由", "従業員", "日時", "取消"])
            for m in moves:
                rows.append([m.business_day.date, m.kind, m.amount, m.reason, m.employee.display_name if m.employee else "", m.occurred_at.isoformat(), "取消" if m.is_voided else ""])
            return _csv_response(request, f"cash_{tag}.csv", ["営業日", "状態", "開始釣銭", "現金売上", "カード売上", "未収計上", "その他入金", "現金経費", "日払い", "その他出金", "途中回収", "現金返金", "理論現金", "実際現金", "差額", "差額理由", "翌日釣銭", "取り分け", "不足"], rows, "export.cash")
        if kind == "backs":
            qs = BackEntry.objects.filter(store=store, business_date__gte=f, business_date__lte=t).select_related("employee", "visit", "order_line").order_by("business_date", "id")
            rows = [[b.business_date, b.employee.display_name, b.kind, b.visit.slip_number if b.visit else "", b.order_line.name if b.order_line else "", b.basis_amount, b.share_percent, b.amount, b.status, b.confirm_timing, b.reason] for b in qs]
            return _csv_response(request, f"backs_{tag}.csv", ["営業日", "従業員", "種別", "伝票番号", "明細", "基準額", "分配%", "金額", "状態", "確定時点", "理由"], rows, "export.backs")
        if kind == "attendance":
            qs = Shift.objects.filter(store=store, business_date__gte=f, business_date__lte=t).select_related("employee").prefetch_related("breaks").order_by("clock_in")
            rows = [[s.business_date, s.employee.display_name, s.clock_in.isoformat(), s.clock_out.isoformat() if s.clock_out else "", shift_minutes(s), s.note] for s in qs]
            return _csv_response(request, f"attendance_{tag}.csv", ["営業日", "従業員", "出勤", "退勤", "勤務分（休憩除く）", "メモ"], rows, "export.attendance")
        if kind == "payroll":
            qs = EmployeePayment.objects.filter(store=store, business_date__gte=f, business_date__lte=t).select_related("employee").order_by("paid_at")
            rows = [[p.business_date, p.employee.display_name, p.kind, p.method, p.amount, "取消" if p.is_voided else "", p.cash_movement_id or "", p.note] for p in qs]
            return _csv_response(request, f"employee_payments_{tag}.csv", ["営業日", "従業員", "種別", "方法", "金額", "取消", "現金出金ID", "メモ"], rows, "export.payroll")
        if kind == "inventory":
            qs = StockMovement.objects.filter(store=store, occurred_at__date__gte=f, occurred_at__date__lte=t).select_related("product").order_by("occurred_at")
            rows = [[m.occurred_at.isoformat(), m.product.name, m.kind, m.quantity, "理論" if m.is_theoretical else "", m.reference, m.reason] for m in qs]
            return _csv_response(request, f"inventory_{tag}.csv", ["日時", "商品", "種別", "増減（本）", "理論消費", "参照", "理由"], rows, "export.inventory")
        if kind == "purchases":
            qs = PurchaseOrder.objects.filter(store=store, created_at__date__gte=f, created_at__date__lte=t).select_related("supplier").prefetch_related("lines__product").order_by("id")
            rows = []
            for po in qs:
                for l in po.lines.all():
                    rows.append([po.number, po.supplier.name, po.status, l.product.name, l.order_units, l.units_per_order_unit, l.unit_price, l.received_bottles, po.ordered_at.isoformat() if po.ordered_at else ""])
            return _csv_response(request, f"purchases_{tag}.csv", ["発注番号", "仕入先", "状態", "商品", "発注数", "本/単位", "単価", "入荷済本数", "発注日時"], rows, "export.purchases")
        raise ApiError("not_found", "出力種別が不正です。", status=404)
