"""銀行 API（/api/banking/…）。X-Store-Id の店舗の事業者が対象。権限は banking.view / banking.import / banking.reconcile。"""
from __future__ import annotations

from django.db import transaction
from django.utils.dateparse import parse_date
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounting.uploads import inspect_csv
from apps.accounting.views import date_range, get_taxpayer
from apps.core.audit import audit
from apps.core.exceptions import ApiError
from apps.core.idempotency import idempotent
from apps.core.permissions import CAP_BANKING_IMPORT, CAP_BANKING_RECONCILE, CAP_BANKING_VIEW, StoreCaps

from . import matching, services
from .models import BankAccount, BankImportProfile, BankReconciliation, BankStatementImport, BankTransaction, TxStatus
from .serializers import BankAccountSerializer, BankImportProfileSerializer, BankReconciliationSerializer, BankStatementImportSerializer, BankTransactionSerializer


def _account(request, account_id) -> BankAccount:
    tp = get_taxpayer(request)
    acc = BankAccount.objects.filter(id=account_id, taxpayer=tp).first()
    if acc is None:
        raise ApiError("not_found", "口座が見つかりません。", status=404)
    return acc


def _tx(request, tx_id) -> BankTransaction:
    tp = get_taxpayer(request)
    tx = BankTransaction.objects.filter(id=tx_id, bank_account__taxpayer=tp).select_related("bank_account").first()
    if tx is None:
        raise ApiError("not_found", "明細が見つかりません。", status=404)
    return tx


def _json(request) -> dict:
    return request.data if isinstance(request.data, dict) else {}


def _overrides(data) -> dict:
    """プレビュー・取込で画面から渡す列対応などの上書き。"""
    import json

    out: dict = {}
    cm = data.get("column_map")
    if isinstance(cm, str) and cm:
        try:
            cm = json.loads(cm)
        except ValueError:
            raise ApiError("validation_error", "列の対応の形式が正しくありません。")
    if isinstance(cm, dict) and cm:
        out["column_map"] = {k: (int(v) if v not in (None, "") else None) for k, v in cm.items()}
    for k in ("encoding", "delimiter"):
        if data.get(k):
            out[k] = data[k]
    if data.get("header_rows") not in (None, ""):
        out["header_rows"] = int(data["header_rows"])
    if data.get("footer_rows") not in (None, ""):
        out["footer_rows"] = int(data["footer_rows"])
    return out


# --- 口座 -------------------------------------------------------------------------------------
class AccountsView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_BANKING_VIEW, write_caps=(CAP_BANKING_IMPORT,))]

    def get(self, request):
        tp = get_taxpayer(request)
        return Response(BankAccountSerializer(BankAccount.objects.filter(taxpayer=tp), many=True).data)

    @idempotent
    @transaction.atomic
    def post(self, request):
        tp = get_taxpayer(request)
        ser = BankAccountSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        acc = ser.save(company=tp.company, taxpayer=tp)
        services.ensure_bank_account_account(acc)
        audit(request, "banking.account.create", target=acc, changes={"name": acc.name})
        return Response(BankAccountSerializer(acc).data, status=201)


class AccountDetailView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_BANKING_IMPORT)]

    @transaction.atomic
    def patch(self, request, account_id):
        acc = _account(request, account_id)
        ser = BankAccountSerializer(acc, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        ser.save()
        audit(request, "banking.account.update", target=acc, changes=request.data)
        return Response(BankAccountSerializer(acc).data)


class ProfilesView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_BANKING_VIEW, write_caps=(CAP_BANKING_IMPORT,))]

    def get(self, request):
        tp = get_taxpayer(request)
        return Response(BankImportProfileSerializer(BankImportProfile.objects.filter(taxpayer=tp), many=True).data)

    @transaction.atomic
    def post(self, request):
        tp = get_taxpayer(request)
        ser = BankImportProfileSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        ba = ser.validated_data.get("bank_account")
        if ba is not None and ba.taxpayer_id != tp.id:
            raise ApiError("validation_error", "口座が見つかりません。")
        p = ser.save(taxpayer=tp)
        audit(request, "banking.profile.create", target=p, changes={"name": p.name})
        return Response(BankImportProfileSerializer(p).data, status=201)


class ProfileDetailView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_BANKING_IMPORT)]

    @transaction.atomic
    def patch(self, request, profile_id):
        tp = get_taxpayer(request)
        p = BankImportProfile.objects.filter(id=profile_id, taxpayer=tp).first()
        if p is None:
            raise ApiError("not_found", "列の対応が見つかりません。", status=404)
        ser = BankImportProfileSerializer(p, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        ser.save()
        audit(request, "banking.profile.update", target=p, changes=request.data)
        return Response(BankImportProfileSerializer(p).data)


# --- 取込 -------------------------------------------------------------------------------------
def _profile_from_request(request, acc: BankAccount) -> BankImportProfile | None:
    pid = request.data.get("profile_id")
    if not pid:
        return None
    p = BankImportProfile.objects.filter(id=pid, taxpayer=acc.taxpayer).first()
    if p is None:
        raise ApiError("validation_error", "列の対応が見つかりません。")
    return p


class ImportPreviewView(APIView):
    parser_classes = [MultiPartParser, FormParser]

    def get_permissions(self):
        return [StoreCaps(CAP_BANKING_IMPORT)]

    def post(self, request):
        acc = _account(request, request.data.get("bank_account"))
        data, _enc = inspect_csv(request.FILES.get("file"))
        res = services.preview(acc, data, profile=_profile_from_request(request, acc), overrides=_overrides(request.data))
        return Response(res)


class ImportsView(APIView):
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def get_permissions(self):
        return [StoreCaps(CAP_BANKING_VIEW, write_caps=(CAP_BANKING_IMPORT,))]

    def get(self, request):
        tp = get_taxpayer(request)
        qs = BankStatementImport.objects.filter(bank_account__taxpayer=tp).select_related("bank_account", "imported_by")
        if request.query_params.get("bank_account"):
            qs = qs.filter(bank_account_id=request.query_params["bank_account"])
        return Response(BankStatementImportSerializer(qs[:200], many=True).data)

    @idempotent
    def post(self, request):
        acc = _account(request, request.data.get("bank_account"))
        f = request.FILES.get("file")
        data, _enc = inspect_csv(f)
        imp = services.create_import(acc, request.user, f, data, profile=_profile_from_request(request, acc), overrides=_overrides(request.data),
                                     save_profile=request.data.get("save_profile") not in ("0", "false", False))
        audit(request, "banking.import.create", target=imp, changes={"file": imp.original_name, "sha256": imp.sha256, "rows": imp.rows_imported, "balance_check": imp.balance_check})
        return Response(BankStatementImportSerializer(imp).data, status=201)


class ImportActionView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_BANKING_IMPORT)]

    @idempotent
    def post(self, request, import_id, action):
        tp = get_taxpayer(request)
        imp = BankStatementImport.objects.filter(id=import_id, bank_account__taxpayer=tp).first()
        if imp is None:
            raise ApiError("not_found", "取込が見つかりません。", status=404)
        if action == "commit":
            imp = services.commit_import(imp, request.user)
        elif action == "rollback":
            imp = services.rollback_import(imp, request.user, reason=_json(request).get("reason") or "")
        else:
            raise ApiError("not_found", "操作が見つかりません。", status=404)
        audit(request, f"banking.import.{action}", target=imp, changes={"rows": imp.rows_imported})
        return Response(BankStatementImportSerializer(imp).data)


# --- 明細・照合 --------------------------------------------------------------------------------
class TransactionsView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_BANKING_VIEW)]

    def get(self, request):
        tp = get_taxpayer(request)
        p = request.query_params
        qs = BankTransaction.objects.filter(bank_account__taxpayer=tp, statement_import__status="committed").select_related("bank_account").prefetch_related(
            "reconciliations__store", "reconciliations__counter_transaction__bank_account")
        if p.get("bank_account"):
            qs = qs.filter(bank_account_id=p["bank_account"])
        if p.get("status"):
            statuses = p["status"].split(",")
            if "open" in statuses:
                statuses += [TxStatus.UNMATCHED, TxStatus.SUGGESTED, TxStatus.PARTIAL]
            qs = qs.filter(status__in=statuses)
        f, t = parse_date(p.get("from") or ""), parse_date(p.get("to") or "")
        if f:
            qs = qs.filter(date__gte=f)
        if t:
            qs = qs.filter(date__lte=t)
        if p.get("q"):
            from django.db.models import Q

            qs = qs.filter(Q(description__icontains=p["q"]) | Q(counterparty__icontains=p["q"]))
        if p.get("direction") == "in":
            qs = qs.filter(amount_in__gt=0)
        elif p.get("direction") == "out":
            qs = qs.filter(amount_out__gt=0)
        total = qs.count()
        try:
            page, size = max(1, int(p.get("page") or 1)), min(200, max(1, int(p.get("page_size") or 50)))
        except ValueError:
            raise ApiError("validation_error", "page の指定が不正です。")
        items = qs.order_by("-date", "-row_no")[(page - 1) * size: page * size]
        return Response({"items": BankTransactionSerializer(items, many=True).data, "total": total, "page": page, "page_size": size})


class TransactionCandidatesView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_BANKING_VIEW)]

    def get(self, request, tx_id):
        tx = _tx(request, tx_id)
        return Response({"transaction": BankTransactionSerializer(tx).data, "candidates": matching.candidates_for(tx)})


class TransactionReconcileView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_BANKING_RECONCILE)]

    @idempotent
    def post(self, request, tx_id):
        tx = _tx(request, tx_id)
        allocations = _json(request).get("allocations") or []
        recs = services.confirm(tx, request.user, allocations)
        tx.refresh_from_db()
        audit(request, "banking.reconcile.confirm", target=tx, changes={"allocations": allocations, "reconciliation_ids": [r.id for r in recs]})
        return Response({"transaction": BankTransactionSerializer(tx).data, "reconciliations": BankReconciliationSerializer(recs, many=True).data})


class TransactionIgnoreView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_BANKING_RECONCILE)]

    @idempotent
    def post(self, request, tx_id):
        tx = _tx(request, tx_id)
        body = _json(request)
        tx = services.ignore(tx, request.user, reason=body.get("reason") or "", undo=bool(body.get("undo")))
        audit(request, "banking.transaction.ignore" + (".undo" if body.get("undo") else ""), target=tx, changes={"reason": body.get("reason")})
        return Response(BankTransactionSerializer(tx).data)


class TransactionTransferView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_BANKING_RECONCILE)]

    @idempotent
    def post(self, request, tx_id):
        tx = _tx(request, tx_id)
        other_id = _json(request).get("counter_transaction_id")
        if not other_id:
            raise ApiError("validation_error", "振替の相手の明細を選んでください。")
        recs = services.confirm(tx, request.user, [{"match_type": "transfer", "target_id": other_id, "amount": str(tx.amount), "rule": "transfer_manual"}])
        tx.refresh_from_db()
        audit(request, "banking.transaction.transfer", target=tx, changes={"counter_transaction_id": other_id})
        return Response({"transaction": BankTransactionSerializer(tx).data, "reconciliations": BankReconciliationSerializer(recs, many=True).data})


class ReconciliationVoidView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_BANKING_RECONCILE)]

    @idempotent
    def post(self, request, rec_id):
        tp = get_taxpayer(request)
        rec = BankReconciliation.objects.filter(id=rec_id, bank_transaction__bank_account__taxpayer=tp).first()
        if rec is None:
            raise ApiError("not_found", "照合が見つかりません。", status=404)
        rec = services.void(rec, request.user, reason=_json(request).get("reason") or "")
        audit(request, "banking.reconcile.void", target=rec, changes={"reason": rec.void_reason})
        return Response(BankReconciliationSerializer(rec).data)


class ScanView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_BANKING_RECONCILE)]

    def post(self, request):
        tp = get_taxpayer(request)
        f, t = date_range(request)
        res = matching.scan(tp, f, t)
        audit(request, "banking.scan", changes={"from": f, "to": t, **res})
        return Response(res)


class SummaryView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_BANKING_VIEW)]

    def get(self, request):
        tp = get_taxpayer(request)
        f, t = date_range(request)
        return Response(services.summary(tp, f, t))
