"""領収書・経費登録 API。判断は services、ここは権限・入出力だけ。"""
from __future__ import annotations

from django.db import transaction
from django.utils.dateparse import parse_date
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounting.compensation import taxpayer_for
from apps.accounting.views import evidence_file_response, get_taxpayer
from apps.core.audit import audit
from apps.core.exceptions import ApiError
from apps.core.idempotency import idempotent
from apps.core.models import CompanySettings, Store
from apps.core.money import D
from apps.core.permissions import (
    CAP_ACCOUNTING_BOOKKEEP, CAP_ACCOUNTING_SETTINGS, CAP_ACCOUNTING_VIEW, CAP_EXPENSES_APPROVE, CAP_EXPENSES_SUBMIT, CAP_EXPENSES_VIEW_STORE, StoreCaps,
    ensure_store_access,
)

from . import register_check, services
from .models import ExpenseReceipt, Payable, PayablePayment, ReceiptStatus
from .serializers import ExpenseReceiptEventSerializer, ExpenseReceiptSerializer, ExpenseReceiptStaffSerializer, PayablePaymentSerializer, PayableSerializer


def _is_approver(request) -> bool:
    caps = ensure_store_access(request).caps
    return CAP_EXPENSES_APPROVE in caps or CAP_ACCOUNTING_VIEW in caps


def _serializer(request):
    return ExpenseReceiptSerializer if _is_approver(request) else ExpenseReceiptStaffSerializer


def _get_receipt(request, receipt_id) -> ExpenseReceipt:
    r = services.visible_receipts(request, approver=_is_approver(request)).filter(id=receipt_id).first()
    if r is None:
        raise ApiError("not_found", "領収書が見つかりません。", status=404)
    return r


def _require_approver(request, r: ExpenseReceipt, *, amount=None) -> None:
    """承認権限。店長は自店舗だけ、金額の上限は店舗設定。"""
    access = ensure_store_access(request)
    if CAP_EXPENSES_APPROVE not in access.caps:
        raise ApiError("forbidden", "この操作を行う権限がありません。", status=403)
    if access.role == "manager":
        if r.store_id != request.store.id:
            raise ApiError("forbidden", "店長は自分の店舗の領収書だけ承認できます。", status=403)
        st = getattr(request.store, "settings", None)
        limit = getattr(st, "manager_approve_limit", None) if st else None
        amt = amount if amount is not None else r.effective_amount
        if limit is not None and amt is not None and D(amt) > D(limit):
            raise ApiError("forbidden", f"店長が承認できるのは1件 {D(limit):,.0f}円 までです。オーナー・経理に回してください。", status=403)


def _paginate(request, qs, serializer_cls):
    p = request.query_params
    total = qs.count()
    if p.get("page"):
        try:
            page, size = max(1, int(p["page"])), min(200, max(1, int(p.get("page_size") or 50)))
        except ValueError:
            raise ApiError("validation_error", "page の指定が不正です。")
        items = qs[(page - 1) * size: page * size]
        return Response({"items": serializer_cls(items, many=True).data, "total": total, "page": page, "page_size": size})
    return Response({"items": serializer_cls(qs[:500], many=True).data, "total": total, "page": 1, "page_size": min(total, 500)})


# --- 領収書 ------------------------------------------------------------------------------------
class ReceiptsView(APIView):
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def get_permissions(self):
        return [StoreCaps(write_caps=(CAP_EXPENSES_SUBMIT,))]

    def get(self, request):
        access = ensure_store_access(request)
        if CAP_EXPENSES_VIEW_STORE not in access.caps and not _is_approver(request):
            raise ApiError("forbidden", "この操作を行う権限がありません。", status=403)
        approver = _is_approver(request)
        qs = services.visible_receipts(request, approver=approver)
        p = request.query_params
        if p.get("store") and approver:
            qs = qs.filter(store_id=p["store"])
        if p.get("status"):
            statuses = [s for s in p["status"].split(",") if s in ReceiptStatus.values]
            qs = qs.filter(status__in=statuses)
        if p.get("from"):
            qs = qs.filter(receipt_date__gte=parse_date(p["from"]))
        if p.get("to"):
            qs = qs.filter(receipt_date__lte=parse_date(p["to"]))
        if p.get("payment_method"):
            qs = qs.filter(payment_method=p["payment_method"])
        if p.get("duplicates") == "1":
            qs = qs.exclude(duplicate_candidates=[])
        return _paginate(request, qs.order_by("-created_at"), _serializer(request))

    def post(self, request):
        # multipart は再送のたびに本文（境界文字列）が変わるため @idempotent は使わず、Idempotency-Key を領収書に記録して同じ登録を返す
        ckey = (request.headers.get("Idempotency-Key") or "").strip()[:80]
        if ckey:
            existing = ExpenseReceipt.objects.filter(submitted_by=request.user, client_key=ckey).first()
            if existing is not None:
                data = _serializer(request)(existing).data
                data["poll_after_ms"] = 2000 if existing.status == ReceiptStatus.OCR_RUNNING else 0
                resp = Response(data, status=201)
                resp["Idempotent-Replayed"] = "true"
                return resp
        values = {k: request.data.get(k) for k in ("receipt_date", "vendor", "total_amount", "summary") if request.data.get(k) not in (None, "")}
        r = services.create_receipt(
            request, file=request.FILES.get("file"), payment_method=request.data.get("payment_method") or "store_cash", memo=request.data.get("memo") or "",
            origin=request.data.get("origin") or "", captured_at=request.data.get("captured_at"), values=values, client_key=ckey,
        )
        if request.data.get("submit") in ("1", "true", True):
            r = services.submit(r, request.user)
        audit(request, "expenses.receipt.create", target=r, changes={"payment_method": r.payment_method, "sha256": r.evidence.sha256, "ocr_status": r.ocr_status})
        data = _serializer(request)(r).data
        data["poll_after_ms"] = 2000 if r.status == ReceiptStatus.OCR_RUNNING else 0
        return Response(data, status=201)


class ReceiptDetailView(APIView):
    def get_permissions(self):
        return [StoreCaps(write_caps=(CAP_EXPENSES_SUBMIT,))]

    def get(self, request, receipt_id):
        access = ensure_store_access(request)
        if CAP_EXPENSES_VIEW_STORE not in access.caps and not _is_approver(request):
            raise ApiError("forbidden", "この操作を行う権限がありません。", status=403)
        r = _get_receipt(request, receipt_id)
        data = _serializer(request)(r).data
        if _is_approver(request):
            from . import duplicates

            data["cash_movement_candidates"] = duplicates.cash_movement_candidates(r)
        return Response(data)

    @transaction.atomic
    def patch(self, request, receipt_id):
        r = _get_receipt(request, receipt_id)
        if not _is_approver(request) and r.submitted_by_id != request.user.id and request.store_access.role != "manager":
            raise ApiError("forbidden", "自分が登録した領収書だけ編集できます。", status=403)
        r = services.update_values(r, request.user, request.data, version=request.data.get("version"))
        audit(request, "expenses.receipt.edit", target=r, changes={k: request.data.get(k) for k in request.data if k != "version"})
        return Response(_serializer(request)(r).data)


class ReceiptActionView(APIView):
    def get_permissions(self):
        return [StoreCaps()]

    @idempotent
    def post(self, request, receipt_id, action):
        r = _get_receipt(request, receipt_id)
        access = request.store_access
        data = request.data if isinstance(request.data, dict) else {}
        if action == "submit":
            if CAP_EXPENSES_SUBMIT not in access.caps:
                raise ApiError("forbidden", "この操作を行う権限がありません。", status=403)
            r = services.submit(r, request.user, version=data.get("version"))
        elif action == "void":
            if CAP_EXPENSES_SUBMIT not in access.caps or (not _is_approver(request) and r.submitted_by_id != request.user.id and access.role != "manager"):
                raise ApiError("forbidden", "自分が登録した領収書だけ取り消せます。", status=403)
            r = services.void(r, request.user, reason=data.get("reason") or "")
        elif action == "approve":
            _require_approver(request, r, amount=data.get("amount"))
            if access.role == "manager" and data.get("store") and str(data["store"]) != str(request.store.id):
                raise ApiError("forbidden", "店長は自分の店舗としてだけ承認できます。", status=403)
            r = services.approve(r, request.user, data, request_store=request.store)
        elif action == "return":
            _require_approver(request, r)
            r = services.return_receipt(r, request.user, reason=data.get("reason") or "", version=data.get("version"))
        elif action == "reject":
            _require_approver(request, r)
            r = services.reject(r, request.user, reason=data.get("reason") or "", version=data.get("version"))
        elif action == "correct":
            _require_approver(request, r, amount=data.get("amount"))
            r = services.correct(r, request.user, data)
        else:
            raise ApiError("not_found", "操作が見つかりません。", status=404)
        audit(request, f"expenses.receipt.{action}", target=r, changes={"status": r.status, "amount": str(r.effective_amount) if r.effective_amount is not None else None,
                                                                         "reason": data.get("reason") or data.get("duplicate_override_reason")})
        return Response(_serializer(request)(r).data)


class ReceiptOcrRetryView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_EXPENSES_SUBMIT)]

    def post(self, request, receipt_id):
        r = _get_receipt(request, receipt_id)
        services.retry_ocr(r, request.user)
        r.refresh_from_db()
        audit(request, "expenses.receipt.ocr_retry", target=r)
        return Response(_serializer(request)(r).data)


class ReceiptEventsView(APIView):
    def get_permissions(self):
        return [StoreCaps()]

    def get(self, request, receipt_id):
        access = ensure_store_access(request)
        if CAP_EXPENSES_VIEW_STORE not in access.caps and not _is_approver(request):
            raise ApiError("forbidden", "この操作を行う権限がありません。", status=403)
        r = _get_receipt(request, receipt_id)
        events = r.events.select_related("actor")
        if not _is_approver(request):
            # スタッフには承認側のメモ（科目など）を見せない
            rows = [{"id": e.id, "action": e.action, "actor_name": str(e.actor) if e.actor else "システム", "at": e.at, "reason": e.reason, "before": {}, "after": {}} for e in events]
            return Response(rows)
        return Response(ExpenseReceiptEventSerializer(events, many=True).data)


class ReceiptFileView(APIView):
    def get_permissions(self):
        return [StoreCaps()]

    def get(self, request, receipt_id):
        access = ensure_store_access(request)
        if CAP_EXPENSES_VIEW_STORE not in access.caps and not _is_approver(request):
            raise ApiError("forbidden", "この操作を行う権限がありません。", status=403)
        r = _get_receipt(request, receipt_id)
        audit(request, "expenses.receipt.file", target=r)
        return evidence_file_response(r.evidence, inline=request.query_params.get("inline") == "1")


class PendingCountView(APIView):
    def get_permissions(self):
        return [StoreCaps()]

    def get(self, request):
        access = ensure_store_access(request)
        if CAP_EXPENSES_VIEW_STORE not in access.caps and not _is_approver(request):
            raise ApiError("forbidden", "この操作を行う権限がありません。", status=403)
        qs = services.visible_receipts(request, approver=_is_approver(request))
        counts = {s: 0 for s in ReceiptStatus.values}
        for row in qs.values("status"):
            counts[row["status"]] += 1
        counts["duplicates"] = qs.filter(status="pending").exclude(duplicate_candidates=[]).count()
        counts["ocr_failed"] = qs.filter(ocr_status="failed", status__in=["pending", "draft"]).count()
        return Response(counts)


class DuplicatesView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_EXPENSES_APPROVE)]

    def get(self, request):
        qs = services.visible_receipts(request, approver=True).filter(status__in=["pending", "returned"]).exclude(duplicate_candidates=[])
        return Response({"items": ExpenseReceiptSerializer(qs[:200], many=True).data})


class RegisterCheckView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_ACCOUNTING_VIEW)]

    def get(self, request):
        store = request.store
        if request.query_params.get("store"):
            store = Store.objects.filter(id=request.query_params["store"], company=request.store.company).first()
            if store is None:
                raise ApiError("not_found", "店舗が見つかりません。", status=404)
        d = parse_date(request.query_params.get("date") or "")
        if d is not None:
            return Response(register_check.check_day(store, d))
        f = parse_date(request.query_params.get("from") or "")
        t = parse_date(request.query_params.get("to") or "")
        if f is None or t is None or t < f or (t - f).days > 62:
            raise ApiError("validation_error", "date か、from/to（62日以内）を指定してください。")
        return Response(register_check.check_range(store, f, t))


# --- 買掛金 -------------------------------------------------------------------------------------
class PayablesView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_ACCOUNTING_VIEW, write_caps=(CAP_ACCOUNTING_BOOKKEEP,))]

    def get(self, request):
        tp = get_taxpayer(request)
        qs = Payable.objects.filter(taxpayer=tp).select_related("account", "store", "supplier").prefetch_related("payments")
        p = request.query_params
        if p.get("status"):
            qs = qs.filter(status__in=p["status"].split(","))
        if p.get("from"):
            qs = qs.filter(invoice_date__gte=parse_date(p["from"]))
        if p.get("to"):
            qs = qs.filter(invoice_date__lte=parse_date(p["to"]))
        return _paginate(request, qs, PayableSerializer)

    @idempotent
    def post(self, request):
        tp = get_taxpayer(request)
        p = services.create_payable(tp, request.user, request.data)
        audit(request, "expenses.payable.create", target=p, changes={"counterparty": p.counterparty, "amount": str(p.amount)})
        return Response(PayableSerializer(p).data, status=201)


def _get_payable(request, payable_id) -> Payable:
    tp = get_taxpayer(request)
    p = Payable.objects.filter(id=payable_id, taxpayer=tp).select_related("account", "store").first()
    if p is None:
        raise ApiError("not_found", "買掛金が見つかりません。", status=404)
    return p


class PayableDetailView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_ACCOUNTING_VIEW)]

    def get(self, request, payable_id):
        return Response(PayableSerializer(_get_payable(request, payable_id)).data)


class PayableVoidView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_ACCOUNTING_BOOKKEEP)]

    def post(self, request, payable_id):
        p = services.void_payable(_get_payable(request, payable_id), request.user, reason=request.data.get("reason") or "")
        audit(request, "expenses.payable.void", target=p, changes={"reason": request.data.get("reason")})
        return Response(PayableSerializer(p).data)


class PayablePaymentsView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_ACCOUNTING_BOOKKEEP)]

    @idempotent
    def post(self, request, payable_id):
        p = _get_payable(request, payable_id)
        pay = services.add_payment(p, request.user, request.data, request_store=request.store)
        audit(request, "expenses.payable.payment", target=pay, changes={"amount": str(pay.amount), "method": pay.method})
        return Response(PayableSerializer(_get_payable(request, payable_id)).data, status=201)


class PayablePaymentVoidView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_ACCOUNTING_BOOKKEEP)]

    def post(self, request, payable_id, payment_id):
        p = _get_payable(request, payable_id)
        pay = PayablePayment.objects.filter(id=payment_id, payable=p).first()
        if pay is None:
            raise ApiError("not_found", "支払いが見つかりません。", status=404)
        services.void_payment(pay, request.user, reason=request.data.get("reason") or "")
        audit(request, "expenses.payable.payment.void", target=pay, changes={"reason": request.data.get("reason")})
        return Response(PayableSerializer(_get_payable(request, payable_id)).data)


# --- AI 設定 -------------------------------------------------------------------------------------
class AiSettingsView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_ACCOUNTING_VIEW, write_caps=(CAP_ACCOUNTING_SETTINGS,))]

    def get(self, request):
        return Response(services.ai_availability(request.store.company))

    def put(self, request):
        cs, _ = CompanySettings.objects.get_or_create(company=request.store.company)
        if "ai_enabled" in request.data:
            cs.ai_enabled = bool(request.data["ai_enabled"])
        if "ai_monthly_budget_jpy" in request.data:
            v = D(request.data["ai_monthly_budget_jpy"] or 0)
            if v < 0:
                raise ApiError("validation_error", "予算は0以上で入力してください。")
            cs.ai_monthly_budget_jpy = v
        if "ai_provider" in request.data:
            prov = (request.data["ai_provider"] or "").lower()
            if prov not in ("", "mock", "anthropic", "none"):
                raise ApiError("validation_error", "AI の接続先が不正です。")
            cs.ai_provider = prov
        cs.save()
        audit(request, "expenses.ai_settings", changes={"ai_enabled": cs.ai_enabled, "budget": str(cs.ai_monthly_budget_jpy), "provider": cs.ai_provider})
        return Response(services.ai_availability(request.store.company))


__all__ = ["taxpayer_for"]
