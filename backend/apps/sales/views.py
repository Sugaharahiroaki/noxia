from __future__ import annotations

from datetime import date

from django.db import transaction
from django.db.models import Prefetch, Sum
from django.utils import timezone
from django.utils.dateparse import parse_date
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.cash.services import current_open_day
from apps.catalog.models import Table
from apps.core.audit import audit
from apps.core.business_date import business_date_now, current_business_date
from apps.core.exceptions import ApiError
from apps.core.idempotency import idempotent
from apps.core.money import D, ZERO
from apps.core.permissions import CAP_FLOOR, CAP_SALES_VIEW, CAP_SALES_VOID, StoreCaps

from . import services
from .alarms import alarm_state
from .models import Checkout, CourseEntry, OrderLine, Receivable, Visit
from .pricing import compute_totals, line_inputs_from_orderlines
from .serializers import (
    CheckoutSerializer, CourseEntrySerializer, OrderLineSerializer, ReceivableCollectionSerializer, ReceivableSerializer,
    RefundSerializer, VisitBriefSerializer, VisitSerializer,
)


def _settings(store):
    return services.store_settings(store)


def _alarms_for(entries, now, mode) -> dict:
    out = {}
    for e in entries:
        st = alarm_state(e, now, mode)
        if st:
            out[e.id] = {**st, "planned_end_at": st["planned_end_at"].isoformat()}
    return out


def _visit_qs(store):
    return Visit.objects.filter(store=store).select_related("table", "customer", "primary_employee").prefetch_related(
        Prefetch("course_entries", queryset=CourseEntry.objects.select_related("employee", "course").prefetch_related("events")),
        Prefetch("lines", queryset=OrderLine.objects.select_related("cancelled_by").prefetch_related("back_targets__employee")),
        "checkouts__payments",
    )


class FloorStateView(APIView):
    """営業・卓一覧。保存された終了予定時刻と現在時刻からアラームを再計算して返す。"""

    def get_permissions(self):
        return [StoreCaps(CAP_FLOOR)]

    def get(self, request):
        store = request.store
        st = _settings(store)
        now = timezone.now()
        visits = list(_visit_qs(store).filter(status="open"))
        entries = [e for v in visits for e in v.course_entries.all()]
        alarms = _alarms_for(entries, now, st.alarm_mode)
        running = {}
        for v in visits:
            active = [l for l in v.lines.all() if l.status == "active" and l.checkout_id is None]
            running[v.id] = str(compute_totals(line_inputs_from_orderlines(active), st).total)
        ctx = {"alarms": alarms, "running_totals": running}
        by_table: dict[int, list] = {}
        for v in visits:
            by_table.setdefault(v.table_id, []).append(VisitBriefSerializer(v, context=ctx).data)
        tables = [
            {"id": t.id, "name": t.name, "capacity": t.capacity, "visits": by_table.get(t.id, [])}
            for t in Table.objects.filter(store=store, is_active=True)
        ]
        alarm_list = []
        for v in visits:
            for e in v.course_entries.all():
                a = alarms.get(e.id)
                if a:
                    alarm_list.append({
                        "visit_id": v.id, "slip_number": v.slip_number, "table_name": v.table.name, "entry_id": e.id,
                        "course_name": e.course_name, **a,
                        "label": e.label or f"{e.guest_count}名",  # 対象（人・グループ名）。表示文言は message
                    })
        alarm_list.sort(key=lambda a: a["remaining_seconds"])
        day = current_open_day(store)
        return Response({
            "server_now": now.isoformat(),
            "business_date": current_business_date(store).isoformat(),
            "business_day": {"id": day.id, "date": day.date.isoformat(), "status": day.status, "opening_float": str(day.opening_float)} if day else None,
            "alarm_mode": st.alarm_mode,
            "tables": tables,
            "alarms": alarm_list,
            "open_visit_count": len(visits),
        })


class VisitListView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_FLOOR)]

    def get(self, request):
        store = request.store
        qs = _visit_qs(store)
        status_q = request.query_params.get("status")
        if status_q:
            qs = qs.filter(status=status_q)
        d = request.query_params.get("date")
        if d:
            qs = qs.filter(business_date=parse_date(d))
        st = _settings(store)
        now = timezone.now()
        visits = list(qs[:200])
        entries = [e for v in visits for e in v.course_entries.all()]
        return Response(VisitBriefSerializer(visits, many=True, context={"alarms": _alarms_for(entries, now, st.alarm_mode)}).data)

    @idempotent
    def post(self, request):
        visit = services.open_visit(request.store, request.user, request.data)
        audit(request, "visit.open", target=visit, changes={"table": visit.table.name, "guest_count": visit.guest_count})
        return _visit_response(request, visit.id, status=201)


class VisitDetailView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_FLOOR)]

    def get(self, request, visit_id):
        visit = _visit_qs(request.store).filter(id=visit_id).first()
        if visit is None:
            raise ApiError("not_found", "伝票が見つかりません。", status=404)
        st = _settings(request.store)
        alarms = _alarms_for(list(visit.course_entries.all()), timezone.now(), st.alarm_mode)
        data = VisitSerializer(visit, context={"alarms": alarms}).data
        totals, _ = services.quote(visit)
        data["quote"] = totals.as_dict()
        data["server_now"] = timezone.now().isoformat()
        return Response(data)


def _visit_response(request, visit_id, status=200):
    visit = _visit_qs(request.store).get(pk=visit_id)
    st = _settings(request.store)
    alarms = _alarms_for(list(visit.course_entries.all()), timezone.now(), st.alarm_mode)
    data = VisitSerializer(visit, context={"alarms": alarms}).data
    totals, _ = services.quote(visit)
    data["quote"] = totals.as_dict()
    return Response(data, status=status)


class VisitCourseEntriesView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_FLOOR)]

    @idempotent
    def post(self, request, visit_id):
        entry = services.add_course_entry(request.store, request.user, visit_id, request.data)
        audit(request, "visit.add_course", target=entry.visit, changes={"course": entry.course_name, "guest_count": entry.guest_count})
        return _visit_response(request, visit_id, status=201)


class ExtendView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_FLOOR)]

    @idempotent
    def post(self, request, visit_id, entry_id):
        entry, line = services.extend_entry(request.store, request.user, visit_id, entry_id)
        audit(request, "visit.extend", target=entry.visit, changes={"entry_id": entry.id, "planned_end_at": entry.planned_end_at, "amount": line.amount})
        resp = _visit_response(request, visit_id)
        resp.data["created_line_id"] = line.id
        return resp


class ExtendPreviewView(APIView):
    """延長したときの料金と新しい終了予定時刻（確認画面用）。"""

    def get_permissions(self):
        return [StoreCaps(CAP_FLOOR)]

    def get(self, request, visit_id, entry_id):
        from datetime import timedelta

        entry = CourseEntry.objects.filter(id=entry_id, visit_id=visit_id, store=request.store, is_active=True).select_related("visit__table").first()
        if entry is None:
            raise ApiError("not_found", "対象のコースが見つかりません。", status=404)
        if entry.is_unlimited or entry.planned_end_at is None:
            raise ApiError("not_extendable", "時間無制限のコースは延長できません。", status=409)
        st = _settings(request.store)
        minutes = entry.extension_minutes or 60
        return Response({
            "amount": str(services.course_amount(st, entry.extension_price, entry.guest_count)),
            "minutes": minutes,
            "from_planned_end_at": timezone.localtime(entry.planned_end_at).isoformat(),
            "to_planned_end_at": timezone.localtime(entry.planned_end_at + timedelta(minutes=minutes)).isoformat(),
            "guest_count": entry.guest_count,
            "table_name": entry.visit.table.name,
            "label": entry.label,
        })


class ChangeUnlimitedView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_FLOOR)]

    @idempotent
    def post(self, request, visit_id, entry_id):
        entry, line = services.change_to_unlimited(request.store, request.user, visit_id, entry_id)
        audit(request, "visit.change_unlimited", target=entry.visit, changes={"entry_id": entry.id, "amount": line.amount})
        resp = _visit_response(request, visit_id)
        resp.data["created_line_id"] = line.id
        return resp


class UnlimitedPreviewView(APIView):
    """無制限へ変更したときの料金を事前表示する（未設定ならエラー内容を返す）。"""

    def get_permissions(self):
        return [StoreCaps(CAP_FLOOR)]

    def get(self, request, visit_id, entry_id):
        entry = CourseEntry.objects.filter(id=entry_id, visit_id=visit_id, store=request.store).first()
        if entry is None:
            raise ApiError("not_found", "対象のコースが見つかりません。", status=404)
        st = _settings(request.store)
        unit, label = services.unlimited_change_price(st, entry)
        return Response({"unit_price": str(unit), "amount": str(services.course_amount(st, unit, entry.guest_count)), "label": label,
                         "target_course": st.default_unlimited_course.name})


class VisitLinesView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_FLOOR)]

    @idempotent
    def post(self, request, visit_id):
        kind = request.data.get("kind") or "product"
        if kind == "product":
            line = services.add_product_line(request.store, request.user, visit_id, request.data)
        elif kind in ("nomination", "douhan"):
            line = services.add_fee_line(request.store, request.user, visit_id, kind, request.data)
        elif kind == "discount":
            line = services.add_discount(request.store, request.user, visit_id, request.data)
        else:
            raise ApiError("validation_error", "明細の種別が不正です。")
        audit(request, f"line.add.{kind}", target=line.visit, changes={"line_id": line.id, "name": line.name, "amount": line.amount})
        return _visit_response(request, visit_id, status=201)


MAX_BATCH_ITEMS = 50


def _add_line(request, visit_id, item: dict):
    kind = item.get("kind") or "product"
    if kind == "product":
        return services.add_product_line(request.store, request.user, visit_id, item)
    if kind in ("nomination", "douhan"):
        return services.add_fee_line(request.store, request.user, visit_id, kind, item)
    if kind == "discount":
        return services.add_discount(request.store, request.user, visit_id, item)
    raise ApiError("validation_error", "明細の種別が不正です。")


class VisitLinesBatchView(APIView):
    """
    複数の注文（カート）をまとめて登録する。1件でもエラーなら全部登録しない（原子性）。
    {items: [{kind, product_id, quantity, back_targets, employee_id, ...}]}
    """

    def get_permissions(self):
        return [StoreCaps(CAP_FLOOR)]

    @idempotent
    def post(self, request, visit_id):
        items = request.data.get("items") or []
        if not isinstance(items, list) or not items:
            raise ApiError("validation_error", "注文する商品を選んでください。")
        if len(items) > MAX_BATCH_ITEMS:
            raise ApiError("validation_error", f"一度に注文できるのは {MAX_BATCH_ITEMS} 件までです。")
        with transaction.atomic():
            created = [_add_line(request, visit_id, item) for item in items]
        audit(request, "line.add.batch", target=created[0].visit,
              changes={"line_ids": [l.id for l in created], "names": [l.name for l in created], "total": sum((l.amount for l in created), ZERO)})
        resp = _visit_response(request, visit_id, status=201)
        resp.data["created_line_ids"] = [l.id for l in created]
        return resp


class LinesCancelBatchView(APIView):
    """直前の注文の取り消し（複数明細をまとめて取消。理由と操作者を記録）。"""

    def get_permissions(self):
        return [StoreCaps(CAP_FLOOR)]

    @idempotent
    def post(self, request, visit_id):
        line_ids = request.data.get("line_ids") or []
        reason = (request.data.get("reason") or "").strip()
        if not isinstance(line_ids, list) or not line_ids:
            raise ApiError("validation_error", "取り消す明細を指定してください。")
        if len(line_ids) > MAX_BATCH_ITEMS:
            raise ApiError("validation_error", f"一度に取り消せるのは {MAX_BATCH_ITEMS} 件までです。")
        with transaction.atomic():
            lines = [services.cancel_line(request.store, request.user, visit_id, lid, reason) for lid in line_ids]
        audit(request, "line.cancel.batch", target=lines[0].visit, changes={"line_ids": [l.id for l in lines], "reason": reason})
        return _visit_response(request, visit_id)


class LineCancelView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_FLOOR)]

    @idempotent
    def post(self, request, visit_id, line_id):
        line = services.cancel_line(request.store, request.user, visit_id, line_id, request.data.get("reason", ""))
        audit(request, "line.cancel", target=line.visit, changes={"line_id": line.id, "name": line.name, "reason": line.cancel_reason})
        return _visit_response(request, visit_id)


class LineBackTargetsView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_FLOOR)]

    def put(self, request, visit_id, line_id):
        line = services.set_back_targets(request.store, request.user, visit_id, line_id, request.data.get("back_targets") or [])
        audit(request, "line.back_targets", target=line.visit, changes={"line_id": line.id})
        return _visit_response(request, visit_id)


class MoveTableView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_FLOOR)]

    @idempotent
    def post(self, request, visit_id):
        visit = services.move_table(request.store, request.user, visit_id, request.data.get("table_id"))
        audit(request, "visit.move_table", target=visit, changes={"table": visit.table.name})
        return _visit_response(request, visit_id)


class VisitCancelView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_FLOOR)]

    @idempotent
    def post(self, request, visit_id):
        visit = services.cancel_visit(request.store, request.user, visit_id, request.data.get("reason", ""))
        audit(request, "visit.cancel", target=visit, changes={"reason": visit.cancel_reason})
        return _visit_response(request, visit_id)


class VisitQuoteView(APIView):
    """会計前の金額見積（明細の一部を選ぶと分割・個別会計の金額）。"""

    def get_permissions(self):
        return [StoreCaps(CAP_FLOOR)]

    def post(self, request, visit_id):
        visit = Visit.objects.filter(id=visit_id, store=request.store).first()
        if visit is None:
            raise ApiError("not_found", "伝票が見つかりません。", status=404)
        totals, lines = services.quote(visit, request.data.get("line_ids"))
        data = totals.as_dict()
        data["line_ids"] = [l.id for l in lines]
        return Response(data)


class VisitCheckoutsView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_FLOOR)]

    @idempotent
    def post(self, request, visit_id):
        checkout = services.confirm_checkout(request.store, request.user, visit_id, request.data)
        audit(request, "checkout.confirm", target=checkout, changes={"total": checkout.total, "kind": checkout.kind,
                                                                     "payments": [{"method": p.method, "amount": str(p.amount)} for p in checkout.payments.all()]})
        return Response(CheckoutSerializer(_checkout_qs(request.store).get(pk=checkout.pk)).data, status=201)


def _checkout_qs(store):
    return Checkout.objects.filter(store=store).select_related("visit__table", "confirmed_by", "voided_by").prefetch_related(
        "payments", "refunds", Prefetch("lines", queryset=OrderLine.objects.prefetch_related("back_targets__employee"))
    )


class CheckoutDetailView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_SALES_VIEW)]

    def get(self, request, checkout_id):
        checkout = _checkout_qs(request.store).filter(id=checkout_id).first()
        if checkout is None:
            raise ApiError("not_found", "会計が見つかりません。", status=404)
        return Response(CheckoutSerializer(checkout).data)


class CheckoutHandwriteView(APIView):
    """
    手書き領収書への転記用。会計で確定した金額をそのまま返す（再計算しない）。
    分割会計の場合は、この会計が伝票全体のうちどの範囲かを明示する。
    """

    def get_permissions(self):
        return [StoreCaps(CAP_SALES_VIEW)]

    def get(self, request, checkout_id):
        checkout = _checkout_qs(request.store).filter(id=checkout_id).first()
        if checkout is None:
            raise ApiError("not_found", "会計が見つかりません。", status=404)
        payments = [p for p in checkout.payments.all() if p.status in ("valid", "refunded")]
        cash = sum((p.amount for p in payments if p.method == "cash"), ZERO)
        card = sum((p.amount for p in payments if p.method == "card"), ZERO)
        receivable = sum((p.amount for p in payments if p.method == "receivable"), ZERO)
        tendered = sum((p.tendered or ZERO for p in payments if p.method == "cash"), ZERO)
        change = sum((p.change_amount for p in payments if p.method == "cash"), ZERO)
        siblings = Checkout.objects.filter(visit=checkout.visit, status="confirmed").exclude(id=checkout.id)
        visit_total = sum((c.total for c in siblings), ZERO) + (checkout.total if checkout.status == "confirmed" else ZERO)
        return Response({
            "checkout_id": checkout.id,
            "checkout_number": checkout.number,
            "status": checkout.status,
            "slip_number": checkout.visit.slip_number,
            "table_name": checkout.visit.table.name,
            "business_date": checkout.business_date,
            "confirmed_at": checkout.confirmed_at,
            "kind": checkout.kind,
            "scope_note": "伝票全体の会計です。" if checkout.kind == "full" and not siblings.exists()
            else f"分割会計です。この会計は伝票#{checkout.visit.slip_number} の一部（{len(checkout.lines.all())}明細）です。税額はこの会計分だけです。",
            "total": str(checkout.total),
            "total_excl_tax": str(checkout.total_excl_tax),
            "tax_total": str(checkout.tax_total),
            "tax_breakdown": checkout.tax_breakdown,
            "service_charge": str(checkout.service_charge),
            "discount_total": str(checkout.discount_total),
            "rounding_adjustment": str(checkout.rounding_adjustment),
            "cash_paid": str(cash),
            "card_paid": str(card),
            "receivable_amount": str(receivable),
            "cash_tendered": str(tendered),
            "cash_change": str(change),
            "visit_total_all_checkouts": str(visit_total),
            "other_checkouts": [{"id": c.id, "number": c.number, "total": str(c.total)} for c in siblings],
            "lines": [{"name": l.name, "amount": str(l.amount), "line_type": l.line_type} for l in checkout.lines.all()],
        })


class CheckoutVoidView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_SALES_VOID)]

    @idempotent
    def post(self, request, checkout_id):
        checkout = services.void_checkout(request.store, request.user, checkout_id, request.data.get("reason", ""),
                                          terminal_refund_done=bool(request.data.get("terminal_refund_done")))
        audit(request, "checkout.void", target=checkout, changes={"reason": checkout.void_reason, "total": checkout.total})
        return Response(CheckoutSerializer(_checkout_qs(request.store).get(pk=checkout.pk)).data)


class CheckoutRefundsView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_SALES_VOID)]

    @idempotent
    def post(self, request, checkout_id):
        refund = services.refund_checkout(request.store, request.user, checkout_id, request.data)
        audit(request, "checkout.refund", target=refund.checkout, changes={"amount": refund.amount, "method": refund.method, "reason": refund.reason})
        return Response(RefundSerializer(refund).data, status=201)


class ReceivableListView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_SALES_VIEW)]

    def get(self, request):
        qs = Receivable.objects.filter(store=request.store).select_related("checkout", "customer").prefetch_related("collections")
        status_q = request.query_params.get("status")
        if status_q:
            qs = qs.filter(status=status_q)
        return Response(ReceivableSerializer(qs[:300], many=True).data)


class ReceivableCollectView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_FLOOR)]

    @idempotent
    def post(self, request, receivable_id):
        coll = services.collect_receivable(request.store, request.user, receivable_id, request.data)
        audit(request, "receivable.collect", target=coll.receivable, changes={"amount": coll.amount, "method": coll.method})
        return Response(ReceivableSerializer(Receivable.objects.prefetch_related("collections").get(pk=coll.receivable_id)).data, status=201)


def _parse_range(request) -> tuple[date, date]:
    store = request.store
    today = current_business_date(store)
    d = request.query_params.get("date")
    if d:
        p = parse_date(d)
        if p is None:
            raise ApiError("validation_error", "日付の形式が正しくありません。")
        return p, p
    f = parse_date(request.query_params.get("from") or "") or today
    t = parse_date(request.query_params.get("to") or "") or f
    if t < f:
        f, t = t, f
    return f, t


class SalesHistoryView(APIView):
    """売上・会計履歴（営業日単位）。"""

    def get_permissions(self):
        return [StoreCaps(CAP_SALES_VIEW)]

    def get(self, request):
        f, t = _parse_range(request)
        qs = _checkout_qs(request.store).filter(business_date__gte=f, business_date__lte=t).order_by("-confirmed_at")
        checkouts = list(qs[:500])
        confirmed = [c for c in checkouts if c.status == "confirmed"]
        pay = {"cash": ZERO, "card": ZERO, "receivable": ZERO}
        for c in confirmed:
            for p in c.payments.all():
                if p.status == "valid":
                    pay[p.method] += p.amount
        refunds = sum((r.amount for c in confirmed for r in c.refunds.all()), ZERO)
        guest_count = Visit.objects.filter(store=request.store, business_date__gte=f, business_date__lte=t).exclude(status="cancelled").aggregate(s=Sum("guest_count"))["s"] or 0
        total = sum((c.total for c in confirmed), ZERO)
        return Response({
            "from": f, "to": t,
            "summary": {
                "checkout_count": len(confirmed), "voided_count": len(checkouts) - len(confirmed), "total": str(total),
                "cash": str(pay["cash"]), "card": str(pay["card"]), "receivable": str(pay["receivable"]), "refunds": str(refunds),
                "guest_count": guest_count, "avg_per_guest": str((total / guest_count).quantize(D("1"))) if guest_count else None,
            },
            "checkouts": CheckoutSerializer(checkouts, many=True).data,
        })
