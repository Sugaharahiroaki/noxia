"""
経理 API。店舗スコープ（X-Store-Id）で会社を決め、事業者はその店舗の事業者（または同じ会社の taxpayer_id）を使う。

権限（core.permissions）:
  tax.view 閲覧 / tax.decide 経営者の判断（税務区分の確定・月次確認・訂正・納付記録）/ tax.advise 税理士等のコメント
  accounting.view 閲覧 / accounting.bookkeep 記帳（経費・証憑・仕訳・照合）/ accounting.freee freee 連携 / accounting.settings 事業者・権限
"""
from __future__ import annotations

import csv
import hashlib
import io
import mimetypes
from datetime import date

from django.conf import settings
from django.db import transaction
from django.db.models import F, Q
from django.http import FileResponse, HttpResponse
from django.utils import timezone
from django.utils.dateparse import parse_date
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.audit import audit
from apps.core.business_date import current_business_date
from apps.core.exceptions import ApiError
from apps.core.idempotency import idempotent
from apps.core.models import Store, User
from apps.core.money import D
from apps.core.permissions import (
    CAP_ACCOUNTING_BOOKKEEP, CAP_ACCOUNTING_FREEE, CAP_ACCOUNTING_SETTINGS, CAP_ACCOUNTING_VIEW, CAP_CONTRACTS_EDIT, CAP_PAYROLL_PAY,
    CAP_PAYROLL_VIEW_ALL, CAP_PAYROLL_VIEW_SELF, CAP_TAX_ADVISE, CAP_TAX_DECIDE, CAP_TAX_VIEW, StoreCaps, ensure_store_access, is_reauthenticated,
)
from apps.staff.models import Employee

from . import compensation, exports as exports_mod, freee, journals, jp_holidays, pdf, reports, services
from .models import (
    AccountingAccessGrant, AccountMapping, CardSettlement, CastContract, Evidence, Expense, FreeeConnection, FreeeSync, FreeeSyncLog, Journal,
    OwnerFundTransfer, PayItemSetting, RemittanceMethod, SalaryWithholdingEntry, StoreTaxProfile, TaxCategory, TaxPayer, TaxRemittance,
    TaxReviewStatus, WithholdingAdjustment, WithholdingMonth, WithholdingRecord, WithholdingRule,
)
from .serializers import (
    AdjustmentSerializer, CardSettlementSerializer, CastContractSerializer, EvidenceSerializer, ExpenseSerializer, GrantSerializer,
    JournalSerializer, OwnerFundTransferSerializer, SalaryEntrySerializer, TaxPayerSerializer, WithholdingRuleSerializer,
)

ETAX_LINKS = [
    {"label": "e-Tax（国税電子申告・納税システム）", "url": "https://www.e-tax.nta.go.jp/"},
    {"label": "e-Tax ソフト（WEB版）", "url": "https://clientweb.e-tax.nta.go.jp/"},
    {"label": "国税庁: ホステス等に支払う報酬・料金（No.2807）", "url": "https://www.nta.go.jp/taxes/shiraberu/taxanswer/gensen/2807.htm"},
    {"label": "国税庁: 納付書の記載のしかた（報酬・料金等）", "url": "https://www.nta.go.jp/publication/pamph/gensen/keisansho/pdf/06.pdf"},
]


# --- 共通 -------------------------------------------------------------------------
def require_any(request, *caps: str):
    access = ensure_store_access(request)
    if not any(c in access.caps for c in caps):
        raise ApiError("forbidden", "この操作を行う権限がありません。", status=403)
    if request.method not in ("GET", "HEAD", "OPTIONS") and access.read_only:
        raise ApiError("store_inactive", "停止中の店舗では更新できません。", status=403)


def require_reauth(request):
    if not is_reauthenticated(request):
        raise ApiError("reauth_required", "この操作にはパスワードの再確認が必要です。", status=403)


def get_taxpayer(request, *, required: bool = True) -> TaxPayer | None:
    tid = request.query_params.get("taxpayer_id") or (request.data.get("taxpayer_id") if hasattr(request, "data") and isinstance(request.data, dict) else None)
    if tid:
        tp = TaxPayer.objects.filter(id=tid, company=request.store.company).first()
        if tp is None:
            raise ApiError("not_found", "事業者が見つかりません。", status=404)
        return tp
    tp = compensation.taxpayer_for(request.store)
    if tp is None and required:
        raise ApiError("taxpayer_required", "この店舗の事業者（源泉徴収義務者）が未設定です。「経理設定」で事業者を登録し、店舗に設定してください。", status=409)
    return tp


def parse_month(value) -> date:
    try:
        y, m = str(value).split("-")[:2]
        return date(int(y), int(m), 1)
    except (TypeError, ValueError):
        raise ApiError("validation_error", "対象年月を YYYY-MM の形式で指定してください。")


def parse_category(value) -> str:
    c = value or TaxCategory.HOSTESS_FEE
    if c not in (TaxCategory.HOSTESS_FEE, TaxCategory.SALARY):
        raise ApiError("validation_error", "区分は hostess_fee か salary を指定してください。")
    return c


def date_range(request) -> tuple[date, date]:
    today = current_business_date(request.store)
    body = request.data if isinstance(request.data, dict) else {}
    f = parse_date(request.query_params.get("from") or body.get("from") or "")
    t = parse_date(request.query_params.get("to") or body.get("to") or "")
    f = f or today.replace(day=1)
    t = t or jp_holidays.month_end(f)
    if t < f:
        raise ApiError("validation_error", "期間の終わりは始まり以降にしてください。")
    if (t - f).days > 400:
        raise ApiError("validation_error", "期間は400日以内で指定してください。")
    return f, t


def csv_response(request, filename: str, header: list[str], rows, action: str) -> HttpResponse:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    for r in rows:
        w.writerow(r)
    resp = HttpResponse("﻿" + buf.getvalue(), content_type="text/csv; charset=utf-8")
    resp["Content-Disposition"] = f'attachment; filename="{filename}"'
    audit(request, action, changes={"filename": filename})
    return resp


def pdf_response(request, filename: str, content: bytes, action: str) -> HttpResponse:
    resp = HttpResponse(content, content_type="application/pdf")
    resp["Content-Disposition"] = f'attachment; filename="{filename}"'
    audit(request, action, changes={"filename": filename})
    return resp


def _employee(request, employee_id) -> Employee:
    emp = Employee.objects.filter(id=employee_id, company=request.store.company).first()
    if emp is None:
        raise ApiError("not_found", "従業員が見つかりません。", status=404)
    return emp


# --- 概要・事業者・権限 ----------------------------------------------------------------
class OverviewView(APIView):
    def get_permissions(self):
        return [StoreCaps()]

    def get(self, request):
        require_any(request, CAP_TAX_VIEW, CAP_ACCOUNTING_VIEW)
        tp = get_taxpayer(request, required=False)
        caps = request.store_access.caps
        data = {
            "taxpayer": TaxPayerSerializer(tp).data if tp else None,
            "taxpayers": TaxPayerSerializer(TaxPayer.objects.filter(company=request.store.company), many=True).data,
            "stores": [{"id": s.id, "name": s.name, "taxpayer_id": getattr(getattr(s, "tax_profile", None), "taxpayer_id", None),
                        "withholding_enabled": compensation.withholding_enabled(s)}
                       for s in Store.objects.filter(company=request.store.company).select_related("tax_profile", "settings")],
            "alerts": services.alerts(tp) if tp and CAP_TAX_VIEW in caps else [],
            "etax_links": ETAX_LINKS,
            "role": request.store_access.role,
            "freee_live_enabled": freee.live_enabled(),
        }
        data["counts"] = _overview_counts(tp) if tp else {}
        data["month_pl"] = None
        if tp:
            today = current_business_date(request.store)
            start = today.replace(day=1)
            try:
                pl = journals.profit_and_loss(tp, start, jp_holidays.month_end(start))
                data["month_pl"] = {"from": pl["from"], "to": pl["to"], "total_income": pl["total_income"], "total_expense": pl["total_expense"],
                                    "profit": pl["profit"], "suspense_total": pl.get("suspense_total", "0")}
            except Exception:  # noqa: BLE001 - 集計に失敗してもダッシュボードは表示する
                data["month_pl"] = None
        return Response(data)


def _overview_counts(tp: TaxPayer) -> dict:
    """ダッシュボード用の未処理件数。ほかのアプリ（領収書・銀行）が未導入でも 0 で返す。"""
    from datetime import timedelta

    from apps.cash.models import BusinessDay, CashMovement

    from . import books
    from .models import Journal, StoreTaxProfile

    store_ids = list(StoreTaxProfile.objects.filter(taxpayer=tp).values_list("store_id", flat=True))
    today = timezone.localdate()
    counts: dict = {"pending_receipts": 0, "returned_receipts": 0, "unmatched_bank": 0, "open_days": 0, "suspense_journals": 0,
                    "unassigned_cash_expenses": 0, "fiscal_years": []}
    try:
        from apps.expenses.models import ExpenseReceipt

        qs = ExpenseReceipt.objects.filter(store_id__in=store_ids)
        counts["pending_receipts"] = qs.filter(status__in=["pending", "returned", "ocr_running"]).count()
        counts["returned_receipts"] = qs.filter(status="returned").count()
    except Exception:  # noqa: BLE001
        pass
    try:
        from apps.banking.models import BankTransaction

        counts["unmatched_bank"] = BankTransaction.objects.filter(bank_account__taxpayer=tp, status__in=["unmatched", "suggested"]).count()
    except Exception:  # noqa: BLE001
        pass
    try:
        counts["open_days"] = BusinessDay.objects.filter(store_id__in=store_ids, status="open").count()
        counts["suspense_journals"] = (
            Journal.objects.filter(taxpayer=tp, is_void=False, issue_date__year=today.year, entry_lines__account__key__startswith="suspense").distinct().count()
        )
        counts["unassigned_cash_expenses"] = CashMovement.objects.filter(
            store_id__in=store_ids, kind="expense", is_voided=False, account__isnull=True, business_day__date__gte=today - timedelta(days=90)
        ).count()
        counts["fiscal_years"] = [books.year_payload(fy) for fy in books.FiscalYear.objects.filter(taxpayer=tp).prefetch_related("events")[:5]]
    except Exception:  # noqa: BLE001
        pass
    return counts


class TaxPayersView(APIView):
    def get_permissions(self):
        return [StoreCaps(write_caps=(CAP_ACCOUNTING_SETTINGS,))]

    def get(self, request):
        require_any(request, CAP_TAX_VIEW, CAP_ACCOUNTING_VIEW)
        return Response(TaxPayerSerializer(TaxPayer.objects.filter(company=request.store.company), many=True).data)

    @transaction.atomic
    def post(self, request):
        ser = TaxPayerSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        tp = ser.save(company=request.store.company)
        audit(request, "accounting.taxpayer.create", target=tp, changes={"name": tp.name, "entity_type": tp.entity_type})
        return Response(TaxPayerSerializer(tp).data, status=201)


class TaxPayerDetailView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_ACCOUNTING_SETTINGS)]

    @transaction.atomic
    def patch(self, request, taxpayer_id):
        tp = TaxPayer.objects.filter(id=taxpayer_id, company=request.store.company).first()
        if tp is None:
            raise ApiError("not_found", "事業者が見つかりません。", status=404)
        ser = TaxPayerSerializer(tp, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        ser.save()
        audit(request, "accounting.taxpayer.update", target=tp, changes=request.data)
        return Response(TaxPayerSerializer(tp).data)


class StoreTaxpayerView(APIView):
    """店舗の事業者を設定する（同じ会社の店舗・事業者だけ）。"""

    def get_permissions(self):
        return [StoreCaps(CAP_ACCOUNTING_SETTINGS)]

    @transaction.atomic
    def put(self, request, store_id):
        store = Store.objects.filter(id=store_id, company=request.store.company).first()
        if store is None:
            raise ApiError("not_found", "店舗が見つかりません。", status=404)
        tp = TaxPayer.objects.filter(id=request.data.get("taxpayer_id"), company=request.store.company).first()
        if tp is None:
            raise ApiError("validation_error", "事業者を選んでください。")
        prof, _ = StoreTaxProfile.objects.update_or_create(store=store, defaults={"taxpayer": tp})
        audit(request, "accounting.store_taxpayer", target=store, changes={"taxpayer_id": tp.id, "taxpayer": tp.name})
        return Response({"store_id": store.id, "taxpayer_id": tp.id})


class GrantsView(APIView):
    """記帳代行担当者・税理士等への権限付与（会社オーナー）。対象は既存のユーザー名で指定する。"""

    def get_permissions(self):
        return [StoreCaps(CAP_ACCOUNTING_SETTINGS)]

    def get(self, request):
        return Response(GrantSerializer(AccountingAccessGrant.objects.filter(company=request.store.company).select_related("user"), many=True).data)

    @transaction.atomic
    def post(self, request):
        user = User.objects.filter(username=(request.data.get("username") or "").strip(), is_active=True).first()
        if user is None:
            raise ApiError("validation_error", "ユーザーが見つかりません（担当者のユーザー名を入力してください）。")
        role = request.data.get("role")
        if role not in ("bookkeeper", "tax_advisor"):
            raise ApiError("validation_error", "役割を選んでください。")
        g, _ = AccountingAccessGrant.objects.update_or_create(
            company=request.store.company, user=user,
            defaults={"role": role, "is_active": True, "expires_on": parse_date(request.data.get("expires_on") or "") or None,
                      "note": (request.data.get("note") or "")[:200], "granted_by": request.user},
        )
        audit(request, "accounting.grant", target=g, changes={"username": user.username, "role": role, "expires_on": g.expires_on})
        return Response(GrantSerializer(g).data, status=201)


class GrantDetailView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_ACCOUNTING_SETTINGS)]

    @transaction.atomic
    def patch(self, request, grant_id):
        g = AccountingAccessGrant.objects.filter(id=grant_id, company=request.store.company).first()
        if g is None:
            raise ApiError("not_found", "権限が見つかりません。", status=404)
        if "is_active" in request.data:
            g.is_active = bool(request.data["is_active"])
        if "expires_on" in request.data:
            g.expires_on = parse_date(request.data.get("expires_on") or "") or None
        g.save()
        audit(request, "accounting.grant.update", target=g, changes=request.data)
        return Response(GrantSerializer(g).data)


# --- キャスト契約・報酬項目 -----------------------------------------------------------
class ContractsView(APIView):
    def get_permissions(self):
        return [StoreCaps()]

    def get(self, request):
        require_any(request, CAP_CONTRACTS_EDIT, CAP_TAX_VIEW)
        qs = CastContract.objects.filter(company=request.store.company, is_active=True).select_related("employee", "tax_reviewed_by")
        if request.query_params.get("employee"):
            qs = qs.filter(employee_id=request.query_params["employee"])
        return Response({"contracts": CastContractSerializer(qs, many=True, context={"request": request}).data,
                         "checklist": [{"key": k, "label": v} for k, v in compensation.TAX_CHECKLIST]})

    @idempotent
    @transaction.atomic
    def post(self, request):
        require_any(request, CAP_CONTRACTS_EDIT)
        ser = CastContractSerializer(data=request.data, context={"request": request})
        ser.is_valid(raise_exception=True)
        c = ser.save(company=request.store.company, created_by=request.user)
        audit(request, "accounting.contract.create", target=c, changes={"employee": c.employee.display_name, "contract_type": c.contract_type})
        return Response(CastContractSerializer(c, context={"request": request}).data, status=201)


class ContractDetailView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_CONTRACTS_EDIT)]

    def _get(self, request, contract_id) -> CastContract:
        c = CastContract.objects.filter(id=contract_id, company=request.store.company).first()
        if c is None:
            raise ApiError("not_found", "契約が見つかりません。", status=404)
        return c

    @transaction.atomic
    def patch(self, request, contract_id):
        c = self._get(request, contract_id)
        before = {"contract_type": c.contract_type, "calc_period": c.calc_period, "deductions": c.deductions, "start_date": str(c.start_date),
                  "end_date": str(c.end_date)}
        ser = CastContractSerializer(c, data=request.data, partial=True, context={"request": request})
        ser.is_valid(raise_exception=True)
        ser.save()
        # 契約区分・計算期間が変わったら、税務区分の確認をやり直す（契約名称だけで区分を決めない）
        if c.tax_review_status == TaxReviewStatus.CONFIRMED and (before["contract_type"] != c.contract_type or before["calc_period"] != c.calc_period):
            c.tax_review_status = TaxReviewStatus.UNREVIEWED
            c.save(update_fields=["tax_review_status", "updated_at"])
        audit(request, "accounting.contract.update", target=c, changes={"before": before, "after": request.data})
        return Response(CastContractSerializer(c, context={"request": request}).data)

    @transaction.atomic
    def delete(self, request, contract_id):
        c = self._get(request, contract_id)
        c.is_active = False
        c.save(update_fields=["is_active", "updated_at"])
        audit(request, "accounting.contract.deactivate", target=c)
        return Response(status=204)


class ContractReviewView(APIView):
    """経営者が実態を確認して税務区分を確定する（契約名称だけでは決めない）。"""

    def get_permissions(self):
        return [StoreCaps(CAP_TAX_DECIDE)]

    @transaction.atomic
    def post(self, request, contract_id):
        c = CastContract.objects.select_for_update().filter(id=contract_id, company=request.store.company).first()
        if c is None:
            raise ApiError("not_found", "契約が見つかりません。", status=404)
        category = request.data.get("tax_category")
        status = request.data.get("tax_review_status") or TaxReviewStatus.CONFIRMED
        if category not in TaxCategory.values or status not in TaxReviewStatus.values:
            raise ApiError("validation_error", "税務区分と確認状況を選んでください。")
        if status == TaxReviewStatus.CONFIRMED and category == TaxCategory.UNDETERMINED:
            raise ApiError("validation_error", "「確認済み」にするには給与所得かホステス報酬かを選んでください。")
        note = (request.data.get("review_note") or "").strip()
        if status == TaxReviewStatus.CONFIRMED and not note:
            raise ApiError("validation_error", "確認した内容（判断の理由）をメモに残してください。")
        if status == TaxReviewStatus.CONFIRMED and category == TaxCategory.HOSTESS_FEE and c.calc_period == "unset":
            raise ApiError("validation_error", "ホステス報酬として確定する前に、契約の報酬計算期間を設定してください。")
        checklist = request.data.get("review_checklist") or {}
        valid_keys = {k for k, _ in compensation.TAX_CHECKLIST}
        checklist = {k: bool(v) for k, v in checklist.items() if k in valid_keys} if isinstance(checklist, dict) else {}
        warnings = []
        if category == TaxCategory.HOSTESS_FEE and sum(1 for v in checklist.values() if v) >= 3:
            warnings.append("雇用に近い実態のチェックが多くあります。給与所得に当たらないか、税理士等に確認してください。")
        before = {"tax_category": c.tax_category, "tax_review_status": c.tax_review_status}
        c.tax_category = category
        c.tax_review_status = status
        c.review_checklist = checklist
        c.review_note = note
        c.tax_reviewed_by = request.user
        c.tax_reviewed_at = timezone.now()
        c.save()
        audit(request, "accounting.contract.review", target=c, changes={"before": before, "after": {"tax_category": category, "tax_review_status": status},
                                                                        "checklist": checklist, "note": note})
        data = CastContractSerializer(c, context={"request": request}).data
        data["warnings"] = warnings
        return Response(data)


class ContractAdviceView(APIView):
    """税理士等の確認コメント（判断は経営者）。"""

    def get_permissions(self):
        return [StoreCaps(CAP_TAX_ADVISE)]

    @transaction.atomic
    def post(self, request, contract_id):
        c = CastContract.objects.filter(id=contract_id, company=request.store.company).first()
        if c is None:
            raise ApiError("not_found", "契約が見つかりません。", status=404)
        c.advisor_note = (request.data.get("advisor_note") or "")[:2000]
        c.advisor_noted_by = request.user
        c.advisor_noted_at = timezone.now()
        c.save(update_fields=["advisor_note", "advisor_noted_by", "advisor_noted_at", "updated_at"])
        audit(request, "accounting.contract.advice", target=c, changes={"note": c.advisor_note})
        return Response(CastContractSerializer(c, context={"request": request}).data)


ALLOWED_UPLOAD_TYPES = {"application/pdf", "image/jpeg", "image/png", "image/heic", "image/heif", "image/webp"}


def _read_upload(request) -> tuple:
    """アップロードを中身（マジックバイト）で検査する。戻り値 (file, content_type, sha256, UploadInfo)。"""
    from .uploads import inspect_upload

    f = request.FILES.get("file")
    info = inspect_upload(f)
    f.seek(0)
    return f, info.content_type, info.sha256, info


class ContractDocumentView(APIView):
    parser_classes = [MultiPartParser, FormParser]

    def get_permissions(self):
        return [StoreCaps(CAP_CONTRACTS_EDIT)]

    def get(self, request, contract_id):
        c = CastContract.objects.filter(id=contract_id, company=request.store.company).first()
        if c is None or not c.document:
            raise ApiError("not_found", "契約書がありません。", status=404)
        audit(request, "accounting.contract.document_download", target=c)
        return FileResponse(c.document.open("rb"), as_attachment=True, filename=c.document_name or "contract")

    @transaction.atomic
    def post(self, request, contract_id):
        c = CastContract.objects.filter(id=contract_id, company=request.store.company).first()
        if c is None:
            raise ApiError("not_found", "契約が見つかりません。", status=404)
        f, _, digest, _info = _read_upload(request)
        c.document.save(f"c{c.id}-{digest[:12]}{_ext(f.name)}", f, save=False)
        c.document_name = f.name[:200]
        c.save(update_fields=["document", "document_name", "updated_at"])
        audit(request, "accounting.contract.document_upload", target=c, changes={"name": f.name, "sha256": digest})
        return Response(CastContractSerializer(c, context={"request": request}).data)


def _ext(name: str) -> str:
    import os

    return os.path.splitext(name)[1][:10].lower()


class PayItemsView(APIView):
    def get_permissions(self):
        return [StoreCaps(write_caps=(CAP_TAX_DECIDE,))]

    def get(self, request):
        require_any(request, CAP_TAX_VIEW, CAP_CONTRACTS_EDIT)
        items = compensation.pay_items(request.store.company)
        return Response([{"code": k, **v} for k, v in items.items()])

    @transaction.atomic
    def put(self, request):
        rows = request.data if isinstance(request.data, list) else request.data.get("items") or []
        for r in rows:
            code = r.get("code")
            if code not in compensation.DEFAULT_PAY_ITEMS:
                raise ApiError("validation_error", f"報酬項目 {code} は変更できません。")
            PayItemSetting.objects.update_or_create(
                company=request.store.company, code=code,
                defaults={"label": (r.get("label") or compensation.DEFAULT_PAY_ITEMS[code]["label"])[:60],
                          "withholding_target": bool(r.get("withholding_target", True)), "note": (r.get("note") or "")[:200]},
            )
        audit(request, "accounting.pay_items", changes={"items": rows})
        items = compensation.pay_items(request.store.company)
        return Response([{"code": k, **v} for k, v in items.items()])


# --- 源泉所得税 ------------------------------------------------------------------------
class RulesView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_TAX_VIEW)]

    def get(self, request):
        return Response(WithholdingRuleSerializer(WithholdingRule.objects.all(), many=True).data)


class MonthsView(APIView):
    """年間の月別一覧（法定集計は事業者単位）。"""

    def get_permissions(self):
        return [StoreCaps(CAP_TAX_VIEW)]

    def get(self, request):
        tp = get_taxpayer(request)
        category = parse_category(request.query_params.get("category"))
        year = int(request.query_params.get("year") or timezone.localdate().year)
        seen, rows = set(), []
        for m in range(1, 13):
            d = date(year, m, 1)
            start, _, _ = services.month_period(tp, category, d)
            if start in seen:
                continue
            seen.add(start)
            rows.append(services.month_view(tp, category, d, detail=False))
        return Response({"taxpayer": TaxPayerSerializer(tp).data, "category": category, "year": year, "months": rows})


class MonthView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_TAX_VIEW)]

    def get(self, request):
        tp = get_taxpayer(request)
        category = parse_category(request.query_params.get("category"))
        d = parse_month(request.query_params.get("month"))
        data = services.month_view(tp, category, d)
        data["form"] = reports.hostess_form(tp, d) if category == TaxCategory.HOSTESS_FEE else reports.salary_form(tp, d)
        data["etax_links"] = ETAX_LINKS
        return Response(data)


def _month_obj(tp, category, d) -> WithholdingMonth:
    start, _, _ = services.month_period(tp, category, d)
    m = WithholdingMonth.objects.filter(taxpayer=tp, category=category, period_start=start).first()
    if m is None:
        raise ApiError("not_aggregated", "先に「集計する」を押してください。", status=409)
    return m


class MonthActionView(APIView):
    """集計・経営者確認・納付待ち・税理士コメント。"""

    def get_permissions(self):
        return [StoreCaps(CAP_TAX_VIEW)]

    @idempotent
    def post(self, request, action):
        tp = get_taxpayer(request)
        category = parse_category(request.data.get("category"))
        d = parse_month(request.data.get("month"))
        if action == "aggregate":
            require_any(request, CAP_TAX_DECIDE, CAP_ACCOUNTING_BOOKKEEP)
            m = services.aggregate_month(tp, category, d, request.user)
        elif action == "confirm":
            require_any(request, CAP_TAX_DECIDE)
            require_reauth(request)
            m = services.confirm_month(_month_obj(tp, category, d), request.user)
        elif action == "awaiting":
            require_any(request, CAP_TAX_DECIDE)
            m = services.mark_awaiting_payment(_month_obj(tp, category, d))
        elif action == "advice":
            require_any(request, CAP_TAX_ADVISE)
            m = _month_obj(tp, category, d)
            m.advisor_note = (request.data.get("note") or "")[:2000]
            m.advisor_checked_by = request.user
            m.advisor_checked_at = timezone.now()
            m.save(update_fields=["advisor_note", "advisor_checked_by", "advisor_checked_at", "updated_at"])
        else:
            raise ApiError("not_found", "操作が見つかりません。", status=404)
        audit(request, f"accounting.withholding.month.{action}", target=m, changes={"category": category, "month": d.isoformat(), "snapshot": m.snapshot})
        return Response(services.month_view(tp, category, d))


class RemittancesView(APIView):
    """納付の記録（経営者が e-Tax 等で納付した後に記録する）。"""

    def get_permissions(self):
        return [StoreCaps(CAP_TAX_DECIDE, reauth=True)]

    @idempotent
    def post(self, request):
        tp = get_taxpayer(request)
        category = parse_category(request.data.get("category"))
        d = parse_month(request.data.get("month"))
        m = _month_obj(tp, category, d)
        method = request.data.get("method")
        if method not in RemittanceMethod.values:
            raise ApiError("validation_error", "納付方法を選んでください。")
        paid_from = request.data.get("paid_from") or "bank"
        if paid_from not in ("bank", "cash_register", "owner_personal"):
            raise ApiError("validation_error", "納付に使ったお金を選んでください。")
        paid_on = parse_date(request.data.get("paid_on") or "")
        if paid_on is None:
            raise ApiError("validation_error", "納付日を入力してください。")
        evidence = None
        if request.data.get("evidence_id"):
            evidence = Evidence.objects.filter(id=request.data["evidence_id"], company=request.store.company, is_deleted=False).first()
            if evidence is None:
                raise ApiError("validation_error", "証憑が見つかりません。")
        with transaction.atomic():
            r = services.record_remittance(m, request.user, paid_on=paid_on, amount=D(request.data.get("amount") or 0), method=method,
                                           paid_from=paid_from, note=request.data.get("note") or "", evidence=evidence, store=request.store)
        audit(request, "accounting.withholding.remittance", target=r, changes={"amount": r.amount, "paid_on": r.paid_on, "method": method,
                                                                              "check": services.remittance_check(m)})
        return Response({"remittance": services.remittance_brief(r), "check": services.remittance_check(m), "month": services.month_view(tp, category, d)},
                        status=201)


class RemittanceVoidView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_TAX_DECIDE, reauth=True)]

    @transaction.atomic
    def post(self, request, remittance_id):
        r = TaxRemittance.objects.select_for_update().filter(id=remittance_id, company=request.store.company).first()
        if r is None:
            raise ApiError("not_found", "納付記録が見つかりません。", status=404)
        reason = (request.data.get("reason") or "").strip()
        if not reason or r.is_voided:
            raise ApiError("validation_error", "取消理由を入力してください（取消済みの記録は取り消せません）。")
        if r.cash_movement_id:
            mv = r.cash_movement
            if mv.business_day.status != "open":
                raise ApiError("day_closed", "レジ締め済みの営業日の出金は取り消せません。", status=409)
            mv.is_voided, mv.void_reason, mv.voided_by = True, reason[:200], request.user
            mv.save()
        r.is_voided, r.void_reason = True, reason[:200]
        r.save(update_fields=["is_voided", "void_reason", "updated_at"])
        m = r.month
        if m.status == "paid":
            m.status = "awaiting_payment"
            m.save(update_fields=["status", "updated_at"])
        audit(request, "accounting.withholding.remittance.void", target=r, changes={"reason": reason})
        return Response(services.remittance_brief(r))


class RecordsView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_TAX_VIEW)]

    def get(self, request):
        tp = get_taxpayer(request)
        qs = WithholdingRecord.objects.filter(taxpayer=tp).select_related("store", "employee")
        if request.query_params.get("month"):
            d = parse_month(request.query_params["month"])
            qs = qs.filter(paid_on__gte=d, paid_on__lte=jp_holidays.month_end(d))
        if request.query_params.get("employee"):
            qs = qs.filter(employee_id=request.query_params["employee"])
        if request.query_params.get("status"):
            qs = qs.filter(status=request.query_params["status"])
        return Response([services.record_brief(r) for r in qs[:500]])


class RecordResolveView(APIView):
    """要確認の支払いを経営者が確認して確定する。"""

    def get_permissions(self):
        return [StoreCaps(CAP_TAX_DECIDE, reauth=True)]

    def post(self, request, record_id):
        rec = WithholdingRecord.objects.filter(id=record_id, company=request.store.company).first()
        if rec is None:
            raise ApiError("not_found", "明細が見つかりません。", status=404)
        ps, pe = parse_date(request.data.get("period_start") or ""), parse_date(request.data.get("period_end") or "")
        period = (ps, pe) if ps and pe else None
        if period and pe < ps:
            raise ApiError("validation_error", "計算期間の終了日は開始日以降にしてください。")
        rec = services.resolve_needs_review(rec, request.user, category=request.data.get("category"), period=period, note=request.data.get("note") or "")
        audit(request, "accounting.withholding.resolve", target=rec, changes={"category": rec.tax_category, "status": rec.status, "note": request.data.get("note")})
        return Response(services.record_brief(rec))


class AdjustmentsView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_TAX_DECIDE, reauth=True)]

    @idempotent
    @transaction.atomic
    def post(self, request):
        tp = get_taxpayer(request)
        ser = AdjustmentSerializer(data=request.data, context={"request": request})
        ser.is_valid(raise_exception=True)
        rec = None
        if request.data.get("record_id"):
            rec = WithholdingRecord.objects.filter(id=request.data["record_id"], taxpayer=tp).first()
            if rec is None:
                raise ApiError("validation_error", "元の明細が見つかりません。")
        a = ser.save(company=tp.company, taxpayer=tp, record=rec, created_by=request.user)
        audit(request, "accounting.withholding.adjustment", target=a, changes=ser.data)
        return Response(AdjustmentSerializer(a).data, status=201)


class AdjustmentVoidView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_TAX_DECIDE, reauth=True)]

    @transaction.atomic
    def post(self, request, adjustment_id):
        a = WithholdingAdjustment.objects.filter(id=adjustment_id, company=request.store.company, is_voided=False).first()
        if a is None:
            raise ApiError("not_found", "訂正が見つかりません。", status=404)
        reason = (request.data.get("reason") or "").strip()
        if not reason:
            raise ApiError("validation_error", "取消理由を入力してください。")
        a.is_voided, a.void_reason = True, reason[:200]
        a.save(update_fields=["is_voided", "void_reason", "updated_at"])
        audit(request, "accounting.withholding.adjustment.void", target=a, changes={"reason": reason})
        return Response(AdjustmentSerializer(a).data)


class SalaryEntriesView(APIView):
    """給与の源泉徴収（給与計算ソフトで計算した結果）の入力。"""

    def get_permissions(self):
        return [StoreCaps(CAP_TAX_VIEW)]

    def get(self, request):
        tp = get_taxpayer(request)
        f, t = date_range(request)
        qs = SalaryWithholdingEntry.objects.filter(taxpayer=tp, paid_on__gte=f, paid_on__lte=t).select_related("employee")
        return Response(SalaryEntrySerializer(qs, many=True, context={"request": request}).data)

    @idempotent
    @transaction.atomic
    def post(self, request):
        require_any(request, CAP_TAX_DECIDE, CAP_ACCOUNTING_BOOKKEEP)
        tp = get_taxpayer(request)
        ser = SalaryEntrySerializer(data=request.data, context={"request": request})
        ser.is_valid(raise_exception=True)
        e = ser.save(company=tp.company, taxpayer=tp, store=request.store, created_by=request.user)
        if e.employee_id and e.period_start and e.period_end:
            services._consume_for_salary(None, e, tp, e.employee, e.period_start, e.period_end, e.gross_amount)
        audit(request, "accounting.salary_entry.create", target=e, changes=ser.data)
        return Response(SalaryEntrySerializer(e, context={"request": request}).data, status=201)


class SalaryEntryVoidView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_TAX_VIEW)]

    @transaction.atomic
    def post(self, request, entry_id):
        require_any(request, CAP_TAX_DECIDE, CAP_ACCOUNTING_BOOKKEEP)
        e = SalaryWithholdingEntry.objects.filter(id=entry_id, company=request.store.company, is_voided=False).first()
        if e is None:
            raise ApiError("not_found", "入力が見つかりません。", status=404)
        reason = (request.data.get("reason") or "").strip()
        if not reason:
            raise ApiError("validation_error", "取消理由を入力してください。")
        e.is_voided, e.void_reason = True, reason[:200]
        e.save(update_fields=["is_voided", "void_reason", "updated_at"])
        e.deduction_usages.all().delete()
        audit(request, "accounting.salary_entry.void", target=e, changes={"reason": reason})
        return Response(SalaryEntrySerializer(e, context={"request": request}).data)


class FormView(APIView):
    """徴収高計算書の転記用データ（JSON / PDF）・月次集計 PDF・キャスト別明細 CSV。"""

    def get_permissions(self):
        return [StoreCaps(CAP_TAX_VIEW)]

    def get(self, request, kind):
        tp = get_taxpayer(request)
        category = parse_category(request.query_params.get("category"))
        d = parse_month(request.query_params.get("month"))
        form = reports.hostess_form(tp, d) if category == TaxCategory.HOSTESS_FEE else reports.salary_form(tp, d)
        tag = f"{d:%Y%m}"
        if kind == "form":
            return Response(form)
        if kind == "form.pdf":
            return pdf_response(request, f"withholding_form_{category}_{tag}.pdf", pdf.transcription_pdf(form), "export.withholding_form")
        if kind == "summary.pdf":
            view = services.month_view(tp, category, d)
            return pdf_response(request, f"withholding_summary_{category}_{tag}.pdf", pdf.monthly_summary_pdf(view, form), "export.withholding_summary")
        if kind == "detail.csv":
            start, end, _ = services.month_period(tp, category, d)
            return csv_response(request, f"withholding_detail_{tag}.csv", reports.CAST_DETAIL_HEADER, reports.cast_detail_rows(tp, start, end),
                                "export.withholding_detail")
        raise ApiError("not_found", "出力の種類が見つかりません。", status=404)


class StoreSummaryView(APIView):
    """店舗単位の管理集計。"""

    def get_permissions(self):
        return [StoreCaps()]

    def get(self, request):
        require_any(request, CAP_TAX_VIEW, CAP_PAYROLL_VIEW_ALL)
        f, t = date_range(request)
        return Response(services.store_summary(request.store, f, t))


class AlertsView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_TAX_VIEW)]

    def get(self, request):
        tp = get_taxpayer(request, required=False)
        return Response(services.alerts(tp) if tp else [])


class DailyPayoutPdfView(APIView):
    """日払い精算書 PDF（店舗の1日分、または1人分）。キャストは自分の分だけ。"""

    def get_permissions(self):
        return [StoreCaps()]

    def get(self, request):
        from apps.cash.services import current_open_day
        from apps.staff import payroll

        access = ensure_store_access(request)
        d = parse_date(request.query_params.get("date") or "")
        if d is None:
            day = current_open_day(request.store)
            d = day.date if day else current_business_date(request.store)
        emp_id = request.query_params.get("employee")
        if CAP_PAYROLL_PAY in access.caps:
            emps = [_employee(request, emp_id)] if emp_id else payroll.daily_payout_employees(request.store, d)
        elif CAP_PAYROLL_VIEW_SELF in access.caps:
            own = getattr(request.user, "employee", None)
            if own is None or own.company_id != request.store.company_id or (emp_id and str(own.id) != str(emp_id)):
                raise ApiError("forbidden", "自分の精算書だけ出力できます。", status=403)
            emps = [own]
        else:
            raise ApiError("forbidden", "この操作を行う権限がありません。", status=403)
        rows = [payroll.daily_payout(request.store, e, d, include_rate=False) for e in emps]
        content = pdf.daily_payout_pdf(request.store.name, d.isoformat(), rows, single=bool(emp_id) or len(emps) == 1)
        return pdf_response(request, f"daily_payout_{d:%Y%m%d}{'_' + str(emp_id) if emp_id else ''}.pdf", content, "export.daily_payout_pdf")


# --- 証憑・経費・資金移動・カード入金 --------------------------------------------------------
class EvidencesView(APIView):
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def get_permissions(self):
        return [StoreCaps(CAP_ACCOUNTING_VIEW)]

    def get(self, request):
        qs = Evidence.objects.filter(company=request.store.company)
        p = request.query_params
        if p.get("include_deleted") != "1":
            qs = qs.filter(is_deleted=False)
        # 電子帳簿保存法の検索要件: 取引年月日（範囲）・取引金額（範囲）・取引先
        if p.get("date_from"):
            qs = qs.filter(transaction_date__gte=parse_date(p["date_from"]))
        if p.get("date_to"):
            qs = qs.filter(transaction_date__lte=parse_date(p["date_to"]))
        if p.get("amount_min"):
            qs = qs.filter(amount__gte=D(p["amount_min"]))
        if p.get("amount_max"):
            qs = qs.filter(amount__lte=D(p["amount_max"]))
        if p.get("counterparty"):
            qs = qs.filter(counterparty__icontains=p["counterparty"])
        if p.get("kind"):
            qs = qs.filter(kind=p["kind"])
        if p.get("origin"):
            qs = qs.filter(origin=p["origin"])
        if p.get("page"):
            # ページ送り（電子帳簿保存法の検索で件数が多いとき）。page を指定しないときは従来どおり先頭 500 件の配列
            try:
                page, size = max(1, int(p["page"])), min(200, max(1, int(p.get("page_size") or 50)))
            except ValueError:
                raise ApiError("validation_error", "page の指定が不正です。")
            total = qs.count()
            items = qs[(page - 1) * size: page * size]
            return Response({"items": EvidenceSerializer(items, many=True).data, "total": total, "page": page, "page_size": size})
        return Response(EvidenceSerializer(qs[:500], many=True).data)

    @idempotent
    @transaction.atomic
    def post(self, request):
        require_any(request, CAP_ACCOUNTING_BOOKKEEP, CAP_TAX_DECIDE)
        f, ctype, digest, info = _read_upload(request)
        kind = request.data.get("kind") or "receipt"
        if kind not in dict(Evidence._meta.get_field("kind").choices):
            raise ApiError("validation_error", "証憑の種類を選んでください。")
        tdate = parse_date(request.data.get("transaction_date") or "")
        if tdate is None:
            raise ApiError("validation_error", "取引年月日を入力してください（検索に使います）。")
        amount = D(request.data["amount"]) if request.data.get("amount") not in (None, "") else None
        tp = get_taxpayer(request, required=False)
        ev = create_evidence(request, f, info, kind=kind, taxpayer=tp, transaction_date=tdate, amount=amount,
                             counterparty=request.data.get("counterparty") or "", description=request.data.get("description") or "",
                             origin=request.data.get("origin") or "", captured_at=request.data.get("captured_at"))
        audit(request, "accounting.evidence.upload", target=ev, changes={"name": f.name, "sha256": digest, "amount": amount, "counterparty": ev.counterparty})
        return Response(EvidenceSerializer(ev).data, status=201)


def create_evidence(request, f, info, *, kind: str, taxpayer, transaction_date, amount, counterparty: str = "", description: str = "",
                    origin: str = "", captured_at=None, store=None) -> Evidence:
    """証憑を保存する（領収書ワークフローからも使う）。保存期間は法人 10 年・個人 7 年の目安（取引年の翌年 3/31 起算）。"""
    from django.utils.dateparse import parse_datetime

    tp = taxpayer
    years = 10 if tp and tp.entity_type == "corporation" else 7
    retention = date(transaction_date.year + years + 1, 3, 31) if transaction_date else None
    if origin not in ("paper_photo", "scan", "electronic"):
        origin = "electronic" if info.content_type == "application/pdf" else "paper_photo"
    ev = Evidence(
        company=request.store.company, taxpayer=tp, store=store or request.store, kind=kind, original_name=f.name[:200], content_type=info.content_type,
        size=info.size, sha256=info.sha256, transaction_date=transaction_date, amount=amount, counterparty=(counterparty or "")[:100],
        description=(description or "")[:200], retention_until=retention, uploaded_by=request.user, origin=origin,
        phash=info.phash or "", page_count=info.page_count, image_width=info.width, image_height=info.height, quality_flags=info.quality_flags,
        captured_at=parse_datetime(captured_at) if isinstance(captured_at, str) and captured_at else (captured_at or timezone.now()),
    )
    ev.file.save(f"e{request.store.company_id}-{info.sha256[:16]}{_ext(f.name)}", f, save=False)
    ev.save()
    return ev


class EvidenceFileView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_ACCOUNTING_VIEW)]

    def get(self, request, evidence_id):
        ev = Evidence.objects.filter(id=evidence_id, company=request.store.company).first()
        if ev is None:
            raise ApiError("not_found", "証憑が見つかりません。", status=404)
        audit(request, "accounting.evidence.download", target=ev)
        return evidence_file_response(ev, inline=request.query_params.get("inline") == "1")


def evidence_file_response(ev: Evidence, *, inline: bool = False):
    """原本の配信（権限確認後に呼ぶ）。プレビューは inline、保存は attachment。公開 URL は作らない。"""
    resp = FileResponse(ev.file.open("rb"), as_attachment=not inline, filename=ev.original_name, content_type=ev.content_type or "application/octet-stream")
    resp["X-Content-Type-Options"] = "nosniff"
    resp["Cache-Control"] = "private, no-store"
    return resp


class EvidenceDeleteView(APIView):
    """論理削除（ファイル・履歴は残す）。"""

    def get_permissions(self):
        return [StoreCaps(CAP_ACCOUNTING_BOOKKEEP)]

    @transaction.atomic
    def post(self, request, evidence_id):
        ev = Evidence.objects.filter(id=evidence_id, company=request.store.company, is_deleted=False).first()
        if ev is None:
            raise ApiError("not_found", "証憑が見つかりません。", status=404)
        reason = (request.data.get("reason") or "").strip()
        if not reason:
            raise ApiError("validation_error", "削除の理由を入力してください。")
        ev.is_deleted, ev.deleted_reason, ev.deleted_at, ev.deleted_by = True, reason[:200], timezone.now(), request.user
        ev.save()
        audit(request, "accounting.evidence.delete", target=ev, changes={"reason": reason})
        return Response(EvidenceSerializer(ev).data)


class _SimpleListCreate(APIView):
    """経費・資金移動・カード入金の一覧と登録（訂正は取消＋新規で履歴を残す）。"""

    model = None
    serializer_class = None
    date_field = "date"
    action = ""

    def get_permissions(self):
        return [StoreCaps(CAP_ACCOUNTING_VIEW, write_caps=(CAP_ACCOUNTING_BOOKKEEP,))]

    def get(self, request):
        tp = get_taxpayer(request)
        f, t = date_range(request)
        qs = self.model.objects.filter(taxpayer=tp, **{f"{self.date_field}__gte": f, f"{self.date_field}__lte": t})
        return Response(self.serializer_class(qs, many=True).data)

    def _store_ok(self, request, store):
        if store is not None and store.company_id != request.store.company_id:
            raise ApiError("validation_error", "この会社の店舗を選んでください。")

    def perform(self, request, ser, tp):
        return ser.save(company=tp.company, taxpayer=tp, created_by=request.user)

    @idempotent
    @transaction.atomic
    def post(self, request):
        tp = get_taxpayer(request)
        ser = self.serializer_class(data=request.data)
        ser.is_valid(raise_exception=True)
        self._store_ok(request, ser.validated_data.get("store"))
        obj = self.perform(request, ser, tp)
        audit(request, f"accounting.{self.action}.create", target=obj, changes=self.serializer_class(obj).data)
        return Response(self.serializer_class(obj).data, status=201)


class ExpensesView(_SimpleListCreate):
    model = Expense
    serializer_class = ExpenseSerializer
    action = "expense"

    def perform(self, request, ser, tp):
        ev = ser.validated_data.get("evidence")
        if ev is not None and ev.company_id != tp.company_id:
            raise ApiError("validation_error", "証憑が見つかりません。")
        return super().perform(request, ser, tp)


class OwnerTransfersView(_SimpleListCreate):
    model = OwnerFundTransfer
    serializer_class = OwnerFundTransferSerializer
    action = "owner_transfer"

    def perform(self, request, ser, tp):
        from apps.cash.models import CashMovement
        from apps.cash.services import require_open_business_day

        obj = super().perform(request, ser, tp)
        if obj.method == "cash_register":
            store = obj.store or request.store
            if compensation.taxpayer_for(store) != tp:
                raise ApiError("validation_error", "この事業者の店舗のレジを選んでください。")
            day = require_open_business_day(store)
            obj.cash_movement = CashMovement.objects.create(
                store=store, business_day=day, kind="other_in" if obj.direction == "in" else "other_out", amount=obj.amount,
                reason=("経営者からの資金投入" if obj.direction == "in" else "経営者への資金引出し") + (f" {obj.note}" if obj.note else ""),
                occurred_at=timezone.now(), performed_by=request.user,
            )
            obj.store = store
            obj.save(update_fields=["cash_movement", "store", "updated_at"])
        return obj


class CardSettlementsView(_SimpleListCreate):
    model = CardSettlement
    serializer_class = CardSettlementSerializer
    date_field = "deposit_date"
    action = "card_settlement"


VOIDABLE = {"expenses": Expense, "owner-transfers": OwnerFundTransfer, "card-settlements": CardSettlement}


class VoidView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_ACCOUNTING_BOOKKEEP)]

    @transaction.atomic
    def post(self, request, kind, obj_id):
        model = VOIDABLE.get(kind)
        if model is None:
            raise ApiError("not_found", "対象が見つかりません。", status=404)
        obj = model.objects.select_for_update().filter(id=obj_id, company=request.store.company, is_voided=False).first()
        if obj is None:
            raise ApiError("not_found", "対象が見つかりません（取消済みの可能性があります）。", status=404)
        reason = (request.data.get("reason") or "").strip()
        if not reason:
            raise ApiError("validation_error", "取消理由を入力してください。")
        mv = getattr(obj, "cash_movement", None)
        if mv is not None:
            if mv.business_day.status != "open":
                raise ApiError("day_closed", "レジ締め済みの営業日の出金は取り消せません。営業日を再開してから取り消してください。", status=409)
            mv.is_voided, mv.void_reason, mv.voided_by = True, reason[:200], request.user
            mv.save()
        obj.is_voided, obj.void_reason = True, reason[:200]
        obj.save(update_fields=["is_voided", "void_reason", "updated_at"])
        new = None
        if kind == "expenses" and isinstance(request.data.get("correction"), dict):
            # 訂正: 取消したうえで正しい内容を新しく登録する（元の記録は残る）
            ser = ExpenseSerializer(data={**ExpenseSerializer(obj).data, **request.data["correction"]})
            ser.is_valid(raise_exception=True)
            new = ser.save(company=obj.company, taxpayer=obj.taxpayer, created_by=request.user, corrected_from=obj)
        audit(request, f"accounting.{kind}.void", target=obj, changes={"reason": reason, "correction_id": new.id if new else None})
        return Response({"voided_id": obj.id, "correction": ExpenseSerializer(new).data if new else None})


# --- 仕訳・損益・照合 ----------------------------------------------------------------------
class JournalsView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_ACCOUNTING_VIEW)]

    def get(self, request):
        tp = get_taxpayer(request)
        f, t = date_range(request)
        qs = journals.journals_for(tp, f, t)
        p = request.query_params
        if p.get("entry_kind"):
            qs = qs.filter(entry_kind=p["entry_kind"])
        if p.get("store"):
            qs = qs.filter(store_id=p["store"])
        if p.get("source_type"):
            qs = qs.filter(source_type=p["source_type"])
        if p.get("include_void") != "1":
            qs = qs.filter(is_void=False) if p.get("only_active") == "1" else qs
        if p.get("account"):
            qs = qs.filter(entry_lines__account__key=p["account"]).distinct()
        total = qs.count()
        if p.get("page"):
            try:
                page, size = max(1, int(p["page"])), min(200, max(1, int(p.get("page_size") or 50)))
            except ValueError:
                raise ApiError("validation_error", "page の指定が不正です。")
            qs = qs[(page - 1) * size: page * size]
        else:
            page, size = 1, total
        return Response({"journals": JournalSerializer(qs, many=True).data, "total": total, "page": page, "page_size": size,
                         "accounts": journals.account_names(tp), "taxes": journals.TAXES})


class JournalsBuildView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_ACCOUNTING_BOOKKEEP)]

    @idempotent
    def post(self, request):
        tp = get_taxpayer(request)
        f, t = date_range(request)
        res = journals.build(tp, f, t)
        audit(request, "accounting.journals.build", changes={"from": f, "to": t, **res})
        return Response(res)


class JournalsCsvView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_ACCOUNTING_VIEW)]

    def get(self, request):
        tp = get_taxpayer(request)
        f, t = date_range(request)
        rows = journals.journal_csv_rows(tp, journals.journals_for(tp, f, t))
        return csv_response(request, f"journals_{f:%Y%m%d}_{t:%Y%m%d}.csv", journals.JOURNAL_CSV_HEADER, rows, "export.journals")


class ProfitLossView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_ACCOUNTING_VIEW)]

    def get(self, request):
        tp = get_taxpayer(request)
        f, t = date_range(request)
        store_id = request.query_params.get("store")
        if store_id and not Store.objects.filter(id=store_id, company=request.store.company).exists():
            raise ApiError("not_found", "店舗が見つかりません。", status=404)
        return Response(journals.profit_and_loss(tp, f, t, store_id=int(store_id) if store_id else None))


class ReconcileView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_ACCOUNTING_VIEW)]

    def get(self, request):
        tp = get_taxpayer(request)
        f, t = date_range(request)
        return Response(journals.reconcile(tp, f, t))


# --- freee 連携 ---------------------------------------------------------------------------
def _conn(tp: TaxPayer) -> FreeeConnection:
    conn, _ = FreeeConnection.objects.get_or_create(taxpayer=tp)
    return conn


def _conn_payload(conn: FreeeConnection) -> dict:
    counts = {}
    for s in FreeeSync.objects.filter(journal__taxpayer=conn.taxpayer).values_list("status", flat=True):
        counts[s] = counts.get(s, 0) + 1
    return {
        "mode": conn.mode, "status": conn.status, "freee_company_id": conn.freee_company_id, "freee_company_name": conn.freee_company_name,
        "connected_at": conn.connected_at, "sync_from": conn.sync_from, "token_expires_at": conn.token_expires_at,
        "has_token": bool(conn.access_token), "last_error": conn.last_error, "live_enabled": freee.live_enabled(), "sync_counts": counts,
    }


class FreeeView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_ACCOUNTING_VIEW)]

    def get(self, request):
        tp = get_taxpayer(request)
        return Response(_conn_payload(_conn(tp)))


def _freee_csv_queryset(tp, f, t, *, only_new: bool):
    qs = Journal.objects.filter(taxpayer=tp, issue_date__gte=f, issue_date__lte=t, is_void=False).order_by("issue_date", "id")
    if only_new:
        # 一度も出していないもの、または出した後に内容が変わったもの（訂正・取消→再作成）
        qs = qs.filter(Q(freee_csv_exported_at__isnull=True) | ~Q(freee_csv_exported_revision=F("revision")))
    return qs


class FreeeCsvStatusView(APIView):
    """freee 取込用 CSV の状況: 期間内の仕訳の件数と、まだ出していない（または出した後に変わった）件数。"""

    def get_permissions(self):
        return [StoreCaps(CAP_ACCOUNTING_VIEW)]

    def get(self, request):
        tp = get_taxpayer(request)
        f, t = date_range(request)
        total = _freee_csv_queryset(tp, f, t, only_new=False)
        new = _freee_csv_queryset(tp, f, t, only_new=True)
        last = Journal.objects.filter(taxpayer=tp, freee_csv_exported_at__isnull=False).order_by("-freee_csv_exported_at").values_list("freee_csv_exported_at", flat=True).first()
        changed = total.filter(freee_csv_exported_at__isnull=False).exclude(freee_csv_exported_revision=F("revision")).count()
        return Response({"from": f.isoformat(), "to": t.isoformat(), "total": total.count(), "new": new.count(), "changed": changed,
                         "last_exported_at": last, "can_mark": CAP_ACCOUNTING_FREEE in request.store_access.caps})


class FreeeCsvView(APIView):
    """freee の「他社会計ソフトインポート」で取り込む仕訳 CSV。only_new=1 で未出力分だけ、mark=0 で出力済みの印を付けない。"""

    def get_permissions(self):
        return [StoreCaps(CAP_ACCOUNTING_VIEW)]

    def get(self, request):
        tp = get_taxpayer(request)
        f, t = date_range(request)
        only_new = request.query_params.get("only_new") in ("1", "true")
        mark = request.query_params.get("mark", "1") in ("1", "true") and CAP_ACCOUNTING_FREEE in request.store_access.caps
        qs = _freee_csv_queryset(tp, f, t, only_new=only_new)
        js = list(qs)
        rows = journals.freee_csv_rows(tp, js)
        if mark and js:
            now = timezone.now()
            for j in js:
                Journal.objects.filter(pk=j.pk).update(freee_csv_exported_at=now, freee_csv_exported_revision=j.revision)
        encoding = "cp932" if request.query_params.get("encoding") == "sjis" else "utf-8-sig"
        name = f"freee_journals_{f:%Y%m%d}_{t:%Y%m%d}{'_new' if only_new else ''}.csv"
        resp = csv_response(request, name, journals.FREEE_CSV_HEADER, rows, "export.freee_csv") if encoding == "utf-8-sig" else \
            exports_mod.file_response(request, name, exports_mod.csv_bytes(journals.FREEE_CSV_HEADER, rows, encoding="cp932"), "text/csv; charset=Shift_JIS", "export.freee_csv")
        resp["X-Row-Count"] = str(len(rows))
        resp["X-Journal-Count"] = str(len(js))
        return resp


class FreeeActionView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_ACCOUNTING_FREEE, reauth=True)]

    def post(self, request, action):
        tp = get_taxpayer(request)
        conn = _conn(tp)
        if action == "connect":
            mode = request.data.get("mode") or "mock"
            if mode == "live":
                if not freee.live_enabled():
                    raise ApiError("freee_live_disabled", "freee 本番との接続はサーバー設定で無効です（FREEE_LIVE_ENABLED・クライアントID等）。", status=409)
                conn.mode = "live"
                conn.save(update_fields=["mode", "updated_at"])
                return Response({"authorize_url": freee.authorize_url(request, tp.id)})
            conn.mode, conn.status, conn.freee_company_id, conn.freee_company_name = "mock", "connected", 1, f"{tp.name}（模擬）"
            conn.connected_at, conn.connected_by, conn.last_error = timezone.now(), request.user, ""
            conn.save()
        elif action == "callback":
            st = request.session.get("freee_oauth_state") or {}
            if not st or st.get("state") != request.data.get("state") or st.get("taxpayer_id") != tp.id:
                raise ApiError("oauth_state", "freee からの戻り情報が一致しません。もう一度「freee と接続」からやり直してください。", status=400)
            try:
                freee.exchange_code(conn, request.data.get("code") or "")
                companies = freee.LiveFreeeClient(conn).companies()
            except freee.FreeeError as e:
                conn.status, conn.last_error = "error", str(e)[:300]
                conn.save(update_fields=["status", "last_error", "updated_at"])
                raise ApiError("freee_error", str(e), status=502)
            request.session.pop("freee_oauth_state", None)
            if not conn.freee_company_id and len(companies) == 1:
                conn.freee_company_id = companies[0]["id"]
            name = next((c["name"] for c in companies if c["id"] == conn.freee_company_id), "")
            conn.status, conn.freee_company_name, conn.connected_at, conn.connected_by, conn.last_error = "connected", name, timezone.now(), request.user, ""
            conn.save()
        elif action == "disconnect":
            conn.status, conn.access_token, conn.refresh_token, conn.token_expires_at = "disconnected", "", "", None
            conn.save()
        elif action == "settings":
            if "sync_from" in request.data:
                conn.sync_from = parse_date(request.data.get("sync_from") or "") or None
            if request.data.get("freee_company_id"):
                conn.freee_company_id = int(request.data["freee_company_id"])
            conn.save()
        elif action == "send":
            res = freee.send_pending(conn, request.user, request.data.get("journal_ids") or None)
            audit(request, "accounting.freee.send", changes=res)
            return Response({**_conn_payload(conn), "result": res})
        else:
            raise ApiError("not_found", "操作が見つかりません。", status=404)
        audit(request, f"accounting.freee.{action}", changes={"mode": conn.mode, "status": conn.status})
        return Response(_conn_payload(conn))


class FreeeMasterView(APIView):
    """freee の勘定科目・税区分の一覧（対応付けの選択肢）。"""

    def get_permissions(self):
        return [StoreCaps(CAP_ACCOUNTING_FREEE)]

    def get(self, request, kind):
        conn = _conn(get_taxpayer(request))
        if conn.status != "connected":
            raise ApiError("freee_not_connected", "freee と接続されていません。", status=409)
        client = freee.client_for(conn)
        try:
            data = client.account_items() if kind == "account-items" else client.taxes()
        except freee.FreeeError as e:
            raise ApiError("freee_error", str(e), status=502)
        return Response(data)


class AccountMappingsView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_ACCOUNTING_VIEW, write_caps=(CAP_ACCOUNTING_FREEE,))]

    def get(self, request):
        tp = get_taxpayer(request)
        names = journals.account_names(tp)
        maps = {m.key: m for m in AccountMapping.objects.filter(taxpayer=tp)}
        accounts = [{"key": k, "default_name": names[k], "account_name": maps[k].account_name if k in maps else names[k],
                     "freee_account_item_id": maps[k].freee_account_item_id if k in maps else None} for k in journals.ACCOUNTS]
        taxes = [{"key": k, "label": v, "tax_code": maps[f"tax:{k}"].tax_code if f"tax:{k}" in maps else None,
                  "tax_name": maps[f"tax:{k}"].tax_name if f"tax:{k}" in maps else ""} for k, v in journals.TAXES.items()]
        return Response({"accounts": accounts, "taxes": taxes})

    @transaction.atomic
    def put(self, request):
        tp = get_taxpayer(request)
        for a in request.data.get("accounts") or []:
            if a.get("key") not in journals.ACCOUNTS:
                continue
            AccountMapping.objects.update_or_create(taxpayer=tp, key=a["key"], defaults={
                "account_name": (a.get("account_name") or journals.account_names(tp)[a["key"]])[:60],
                "freee_account_item_id": a.get("freee_account_item_id") or None,
            })
        for t in request.data.get("taxes") or []:
            if t.get("key") not in journals.TAXES:
                continue
            AccountMapping.objects.update_or_create(taxpayer=tp, key=f"tax:{t['key']}", defaults={
                "account_name": journals.TAXES[t["key"]], "tax_code": t.get("tax_code") if t.get("tax_code") not in ("", None) else None,
                "tax_name": (t.get("tax_name") or "")[:60],
            })
        audit(request, "accounting.account_mappings", changes=request.data)
        return self.get(request)


class FreeeSyncResolveView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_ACCOUNTING_FREEE, reauth=True)]

    def post(self, request, sync_id):
        tp = get_taxpayer(request)
        sync = FreeeSync.objects.filter(id=sync_id, journal__taxpayer=tp).first()
        if sync is None:
            raise ApiError("not_found", "連携記録が見つかりません。", status=404)
        fid = request.data.get("freee_id")
        sync = freee.resolve_unknown(sync, freee_id=int(fid) if fid else None, user=request.user)
        audit(request, "accounting.freee.resolve_unknown", target=sync, changes={"freee_id": fid})
        return Response({"id": sync.id, "status": sync.status, "freee_id": sync.freee_id})


class FreeeLogsView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_ACCOUNTING_VIEW)]

    def get(self, request):
        tp = get_taxpayer(request)
        logs = FreeeSyncLog.objects.filter(sync__journal__taxpayer=tp).select_related("sync__journal")[:200]
        return Response([{"id": lg.id, "journal_id": lg.sync.journal_id, "description": lg.sync.journal.description, "action": lg.action, "ok": lg.ok,
                          "http_status": lg.http_status, "message": lg.message, "created_at": lg.created_at} for lg in logs])
