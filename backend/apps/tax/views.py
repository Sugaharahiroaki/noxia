"""確定申告 API（/api/tax/...）。税務上の入力・計算・完成・出力は経営者本人（tax.return.edit / export、明示の再認証）。"""
from __future__ import annotations

import hashlib
import json
import re
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import IntegrityError, transaction
from django.http import FileResponse, HttpResponse
from django.utils import timezone
from django.utils.dateparse import parse_date
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounting import books
from apps.accounting.exports import file_response
from apps.accounting.views import get_taxpayer, require_any
from apps.core.audit import audit
from apps.core.exceptions import ApiError
from apps.core.idempotency import idempotent
from apps.core.permissions import (
    CAP_TAX_CONFIG_MANAGE, CAP_TAX_RETURN_EDIT, CAP_TAX_RETURN_EXPORT, CAP_TAX_RETURN_VIEW, CAP_TAX_VIEW, IsPlatformOperator, StoreCaps,
)

from . import compute as compute_mod
from . import config as config_mod
from .exports import pdf as pdf_mod
from .exports import xlsx as xlsx_mod
from .exports import xtx as xtx_mod
from .guide import build_guide
from .models import GuideProgress, IndividualTaxProfile, TaxAuditLog, TaxExport, TaxReturn, TaxReturnStatus, TaxYearConfiguration, record_tax_audit, verify_chain

NUMBER_FIELDS = ("social_insurance", "small_business_mutual", "donation", "withheld_tax", "estimated_tax_paid", "loss_carryforward", "family_employee",
                 "housing_loan_credit", "casualty_loss")


def _money(v, name):
    try:
        d = Decimal(str(v if v not in (None, "") else 0))
    except (InvalidOperation, ValueError):
        raise ApiError("validation_error", f"{name} は数値で入力してください。", details={name: "数値"})
    if d < 0:
        raise ApiError("validation_error", f"{name} は 0 以上で入力してください。", details={name: "0以上"})
    return str(d.quantize(Decimal(1)))


def validate_inputs(data: dict) -> dict:
    """所得控除などの入力の検証。氏名・個人番号は受け付けない（12 桁の数字は拒否）。"""
    if not isinstance(data, dict):
        raise ApiError("validation_error", "入力の形式が正しくありません。")
    raw = json.dumps(data, ensure_ascii=False)
    if re.search(r"(?<!\d)\d{12}(?!\d)", raw):
        raise ApiError("validation_error", "12 桁の番号（マイナンバーの可能性）は入力できません。国税庁の画面で直接入力してください。")
    out: dict = {}
    for k in NUMBER_FIELDS:
        if k in data:
            out[k] = _money(data.get(k), k)
    sal = data.get("salary") or {}
    out["salary"] = {"revenue": _money(sal.get("revenue"), "給与収入"), "withheld": _money(sal.get("withheld"), "給与の源泉徴収税額"),
                     "social_insurance": _money(sal.get("social_insurance"), "給与から控除された社会保険料")}
    ois = []
    for oi in data.get("other_incomes") or []:
        kind = str(oi.get("kind") or "miscellaneous")[:30]
        ois.append({"kind": kind, "amount": _money(oi.get("amount"), "その他の所得"), "label": str(oi.get("label") or "")[:60]})
    out["other_incomes"] = ois
    li = data.get("life_insurance") or {}
    out["life_insurance"] = {k: _money(li.get(k), k) for k in ("new_general", "old_general", "care", "new_pension", "old_pension")}
    out["life_insurance"]["has_under23"] = bool(li.get("has_under23"))
    eq = data.get("earthquake_insurance") or {}
    out["earthquake_insurance"] = {"earthquake": _money(eq.get("earthquake"), "地震保険料"), "old_long_term": _money(eq.get("old_long_term"), "旧長期損害保険料")}
    med = data.get("medical") or {}
    out["medical"] = {"paid": _money(med.get("paid"), "医療費"), "reimbursed": _money(med.get("reimbursed"), "保険金等")}
    sp = data.get("spouse") or {}
    out["spouse"] = {"has": bool(sp.get("has")), "income": _money(sp.get("income"), "配偶者の所得"), "age70": bool(sp.get("age70"))}
    dp = data.get("dependents") or {}
    out["dependents"] = {k: max(0, int(dp.get(k) or 0)) for k in ("general", "specific", "elderly", "elderly_cohabiting")}
    out["dependents"]["specific_relatives"] = [{"income": _money(r.get("income"), "特定親族の所得")} for r in (dp.get("specific_relatives") or [])][:10]
    pf = data.get("personal") or {}
    out["personal"] = {k: max(0, int(pf.get(k) or 0)) for k in ("disability_general", "disability_special", "disability_special_cohabiting")}
    for k in ("widow", "single_parent", "working_student"):
        out["personal"][k] = bool(pf.get(k))
    out["is_partial_year_return"] = bool(data.get("is_partial_year_return"))
    out["memo"] = str(data.get("memo") or "")[:500]
    return out


def _return_payload(tr: TaxReturn) -> dict:
    fy = tr.fiscal_year
    prof = getattr(tr.taxpayer, "individual_profile", None)
    return {
        "id": tr.id, "year": tr.config.year, "filing_type": tr.filing_type, "status": tr.status, "status_label": tr.get_status_display(),
        "version": tr.version, "inputs": tr.inputs, "business_summary": tr.business_summary, "computed": tr.computed,
        "unsupported_items": tr.unsupported_items, "warnings": tr.warnings, "completed_at": tr.completed_at, "filed_at": tr.filed_at, "filed_note": tr.filed_note,
        "fiscal_year": books.year_payload(fy), "config": {"year": tr.config.year, "status": tr.config.status, "content_hash": tr.config.content_hash},
        "profile": _profile_payload(prof) if prof else None,
        "versions": [{"id": v.id, "version_no": v.version_no, "created_at": v.created_at, "created_by": str(v.created_by) if v.created_by else "", "reason": v.reason,
                      "is_final": v.is_final, "tax_due": v.computed.get("values", {}).get("tax_due"), "refund": v.computed.get("values", {}).get("refund"),
                      "unsupported": len(v.unsupported_items or [])} for v in tr.versions.all()[:30]],
        "xtx_available": xtx_mod.mapping_for(tr.config.year) is not None,
        "xtx_released": tr.config.year in getattr(settings, "TAX_XTX_RELEASED_YEARS", []),
    }


def _profile_payload(p: IndividualTaxProfile) -> dict:
    return {"individual_name": p.individual_name, "trade_name": p.trade_name, "business_start_date": p.business_start_date,
            "blue_approval_status": p.blue_approval_status, "blue_approved_from_year": p.blue_approved_from_year, "blue_application_date": p.blue_application_date,
            "bookkeeping_method": p.bookkeeping_method, "consumption_tax_status": p.consumption_tax_status,
            "invoice_registration_number": p.invoice_registration_number, "note": p.note}


def get_return(request, year: int, *, create: bool = True) -> TaxReturn:
    tp = get_taxpayer(request)
    if tp.entity_type != "sole_proprietor":
        raise ApiError("not_individual", "所得税の確定申告は個人事業主の事業者だけが対象です（法人は対象外）。", status=409)
    cfg = config_mod.ensure_config(year)
    if cfg is None:
        raise ApiError("year_not_supported", f"{year}年分の税制設定がありません。", status=404)
    fy = books.get_fiscal_year(tp, year, create=create)
    if fy is None:
        raise ApiError("not_found", "年度がありません。", status=404)
    qs = TaxReturn.objects.filter(taxpayer=tp, fiscal_year=fy).select_related("config", "fiscal_year", "taxpayer")
    tr = qs.first()
    if tr is None:
        if not create:
            raise ApiError("not_found", "申告書データがありません。", status=404)
        # 画面が同じ年分を同時に 2 回取りに来ることがある（初回表示）。先に作られていたらそれを使う
        try:
            with transaction.atomic():
                tr = TaxReturn.objects.create(taxpayer=tp, fiscal_year=fy, config=cfg, filing_type=fy.filing_type if fy.filing_type in ("white", "blue_10") else "white")
        except IntegrityError:
            tr = qs.first()
            if tr is None:
                raise
    return tr


# --- 申告情報 ---------------------------------------------------------------------------------
class ProfileView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_TAX_RETURN_VIEW, write_caps=(CAP_TAX_RETURN_EDIT,))]

    def get(self, request):
        tp = get_taxpayer(request)
        prof = getattr(tp, "individual_profile", None)
        return Response({"taxpayer_id": tp.id, "entity_type": tp.entity_type, "profile": _profile_payload(prof) if prof else None})

    @transaction.atomic
    def put(self, request):
        tp = get_taxpayer(request)
        if tp.entity_type != "sole_proprietor":
            raise ApiError("not_individual", "個人事業主の事業者だけ設定できます。", status=409)
        prof, _ = IndividualTaxProfile.objects.get_or_create(taxpayer=tp)
        d = request.data
        before = _profile_payload(prof)
        for f in ("individual_name", "trade_name", "note"):
            if f in d:
                setattr(prof, f, str(d[f] or "")[: {"individual_name": 60, "trade_name": 100, "note": 2000}[f]])
        if "business_start_date" in d:
            prof.business_start_date = parse_date(d["business_start_date"] or "") or None
        if "blue_approval_status" in d:
            if d["blue_approval_status"] not in ("none", "applied", "approved", "revoked"):
                raise ApiError("validation_error", "青色申告の承認状況が不正です。")
            prof.blue_approval_status = d["blue_approval_status"]
        if "blue_approved_from_year" in d:
            prof.blue_approved_from_year = int(d["blue_approved_from_year"]) if d["blue_approved_from_year"] else None
        if "blue_application_date" in d:
            prof.blue_application_date = parse_date(d["blue_application_date"] or "") or None
        if "bookkeeping_method" in d and d["bookkeeping_method"] in ("simple", "double"):
            prof.bookkeeping_method = d["bookkeeping_method"]
        if "consumption_tax_status" in d:
            if d["consumption_tax_status"] not in ("exempt", "taxable_general", "taxable_simplified", "taxable_2wari"):
                raise ApiError("validation_error", "消費税の区分が不正です。")
            prof.consumption_tax_status = d["consumption_tax_status"]
        if "invoice_registration_number" in d:
            num = (d["invoice_registration_number"] or "").strip().upper()
            if num and not (len(num) == 14 and num[0] == "T" and num[1:].isdigit()):
                raise ApiError("validation_error", "登録番号は T＋13桁の数字です。")
            prof.invoice_registration_number = num
        prof.save()
        record_tax_audit(tp, "profile_change", user=request.user, detail={"before": {k: str(v) for k, v in before.items()}, "after": {k: str(v) for k, v in _profile_payload(prof).items()}})
        audit(request, "tax.profile.update", target=prof)
        return Response(_profile_payload(prof))


# --- 年度設定 ---------------------------------------------------------------------------------
class YearsView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_TAX_RETURN_VIEW)]

    def get(self, request):
        years = []
        for y in config_mod.available_years():
            cfg = config_mod.ensure_config(y)
            years.append({"year": y, "status": cfg.status, "content_hash": cfg.content_hash, "verified_at": cfg.verified_at,
                          "xtx_available": xtx_mod.mapping_for(y) is not None})
        return Response(years)


class YearConfigView(APIView):
    def get_permissions(self):
        if self.request.method in ("GET", "HEAD", "OPTIONS"):
            return [StoreCaps(CAP_TAX_RETURN_VIEW)]
        return [IsPlatformOperator()]

    def get(self, request, year):
        cfg = config_mod.ensure_config(int(year))
        if cfg is None:
            raise ApiError("not_found", "年度の設定がありません。", status=404)
        return Response(cfg.as_dict())

    @transaction.atomic
    def put(self, request, year):
        from apps.core.permissions import is_reauthenticated

        if not is_reauthenticated(request, explicit=True):
            raise ApiError("reauth_required", "この操作にはパスワードの再確認が必要です。", status=403)
        cfg = config_mod.ensure_config(int(year))
        if cfg is None:
            raise ApiError("not_found", "年度の設定がありません。", status=404)
        before = cfg.content_hash
        for k in ("income_tax", "surtaxes", "deductions", "statement_lines", "guide", "forms", "sources"):
            if k in request.data:
                setattr(cfg, k, request.data[k])
        if "status" in request.data and request.data["status"] in ("draft", "verified", "published"):
            cfg.status = request.data["status"]
            if cfg.status != "draft":
                cfg.verified_by, cfg.verified_at = request.user, timezone.now()
        if "note" in request.data:
            cfg.note = str(request.data["note"] or "")[:4000]
        cfg.save()
        audit(request, "tax.config.update", target=cfg, changes={"year": cfg.year, "before_hash": before, "after_hash": cfg.content_hash}, store=None, company=None)
        return Response(cfg.as_dict())


# --- 申告書データ -----------------------------------------------------------------------------
class ReturnView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_TAX_RETURN_VIEW)]

    def get(self, request, year):
        tr = get_return(request, int(year))
        return Response(_return_payload(tr))


class ReturnInputsView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_TAX_RETURN_EDIT)]

    @transaction.atomic
    def put(self, request, year):
        tr = get_return(request, int(year))
        if tr.status in (TaxReturnStatus.LOCKED, TaxReturnStatus.FILED):
            raise ApiError("locked", "申告済み・ロック済みの申告書は変更できません。", status=409)
        if request.data.get("version") is not None and int(request.data["version"]) != tr.version:
            raise ApiError("version_conflict", "ほかの画面で更新されています。画面を更新してやり直してください。", status=409)
        inputs = validate_inputs(request.data.get("inputs") or {})
        tr.inputs = inputs
        if request.data.get("filing_type") in ("white", "blue_10"):
            tr.filing_type = request.data["filing_type"]
            tr.fiscal_year.filing_type = tr.filing_type
            tr.fiscal_year.save(update_fields=["filing_type", "updated_at"])
        tr.version += 1
        tr.updated_by = request.user
        if tr.status in (TaxReturnStatus.COMPLETE, TaxReturnStatus.EXPORTED):
            tr.status = TaxReturnStatus.DRAFT
        tr.save()
        record_tax_audit(tr.taxpayer, "inputs_update", user=request.user, tax_return=tr, detail={"keys": sorted(inputs.keys())})
        audit(request, "tax.return.inputs", target=tr)
        return Response(_return_payload(tr))


class ReturnComputeView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_TAX_RETURN_EDIT)]

    @idempotent
    def post(self, request, year):
        tr = get_return(request, int(year))
        if tr.status in (TaxReturnStatus.LOCKED, TaxReturnStatus.FILED):
            raise ApiError("locked", "申告済み・ロック済みの申告書は再計算できません。「差し戻す」で作成中に戻してください。", status=409)
        ver = compute_mod.compute_return(tr, request.user, reason=request.data.get("reason") or "計算")
        audit(request, "tax.return.compute", target=tr, changes={"version_no": ver.version_no})
        tr.refresh_from_db()
        data = _return_payload(tr)
        data["version_created"] = {"id": ver.id, "version_no": ver.version_no}
        return Response(data)


class ReturnActionView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_TAX_RETURN_EDIT)]

    @idempotent
    def post(self, request, year, action):
        tr = get_return(request, int(year))
        if action == "complete":
            compute_mod.complete_return(tr, request.user)
        elif action == "reopen":
            compute_mod.reopen_return(tr, request.user, request.data.get("reason") or "")
        elif action == "mark-filed":
            compute_mod.mark_filed(tr, request.user, note=request.data.get("note") or "", version_id=request.data.get("version_id"))
            tr.fiscal_year.refresh_from_db()
        else:
            raise ApiError("not_found", "操作が見つかりません。", status=404)
        audit(request, f"tax.return.{action}", target=tr)
        tr.refresh_from_db()
        return Response(_return_payload(tr))


class VersionsView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_TAX_RETURN_VIEW)]

    def get(self, request, year, version_id=None):
        tr = get_return(request, int(year), create=False)
        if version_id is None:
            return Response(_return_payload(tr)["versions"])
        v = tr.versions.filter(id=version_id).first()
        if v is None:
            raise ApiError("not_found", "版が見つかりません。", status=404)
        return Response({"id": v.id, "version_no": v.version_no, "created_at": v.created_at, "reason": v.reason, "is_final": v.is_final,
                         "inputs": v.inputs, "business_summary": v.business_summary, "computed": v.computed, "unsupported_items": v.unsupported_items,
                         "warnings": v.warnings, "config_hash": v.config_hash, "journal_snapshot_hash": v.journal_snapshot_hash,
                         "exports": [{"id": e.id, "format": e.format, "filename": e.filename, "sha256": e.sha256, "generated_at": e.created_at,
                                      "is_released": e.is_released, "validation": e.validation} for e in v.exports.all()]})


# --- 入力ガイド -------------------------------------------------------------------------------
class GuideView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_TAX_RETURN_VIEW)]

    def get(self, request, year):
        tr = get_return(request, int(year))
        filing = request.query_params.get("filing")
        return Response(build_guide(tr, filing if filing in ("white", "blue") else None))


class GuideProgressView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_TAX_RETURN_VIEW)]

    @transaction.atomic
    def put(self, request, year):
        tr = get_return(request, int(year))
        items = request.data.get("items") if isinstance(request.data, dict) else None
        if items is None:
            items = [request.data]
        for it in items:
            key = str(it.get("item_key") or "")[:60]
            if not key:
                continue
            p, _ = GuideProgress.objects.get_or_create(tax_return=tr, item_key=key)
            if "checked" in it:
                p.checked = bool(it["checked"])
                p.checked_at = timezone.now() if p.checked else None
            if it.get("copied"):
                p.copied_at = timezone.now()
            if "note" in it:
                p.note = str(it["note"] or "")[:200]
            p.save()
        return Response(build_guide(tr)["progress"])


class GuidePdfView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_TAX_RETURN_EXPORT, reauth=True)]

    def get(self, request, year):
        tr = get_return(request, int(year))
        g = build_guide(tr)
        content = pdf_mod.guide_pdf(g, tr.taxpayer.name)
        record_tax_audit(tr.taxpayer, "export", user=request.user, tax_return=tr, detail={"format": "pdf_guide"})
        return file_response(request, f"guide_{year}.pdf", content, "application/pdf", "export.tax_guide")


# --- 出力 ---------------------------------------------------------------------------------------
class ExportsView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_TAX_RETURN_EXPORT, reauth=True)]

    @idempotent
    @transaction.atomic
    def post(self, request, year):
        tr = get_return(request, int(year), create=False)
        fmt = request.data.get("format")
        if fmt not in ("pdf_forms", "xlsx_return", "xtx"):
            raise ApiError("validation_error", "出力の形式を選んでください（pdf_forms / xlsx_return / xtx）。")
        ver = tr.versions.filter(id=request.data.get("version_id")).first() if request.data.get("version_id") else tr.versions.first()
        if ver is None:
            raise ApiError("not_computed", "先に「計算」を実行してください。", status=409)
        if fmt == "xtx":
            if not request.user.is_platform_operator and tr.config.year not in getattr(settings, "TAX_XTX_RELEASED_YEARS", []):
                raise ApiError("xtx_not_released", "XTX は公式ソフトでの取込試験が完了するまで公開していません。入力ガイドで作成コーナーへ転記してください。", status=409)
            content, validation = xtx_mod.generate(ver)
            released = xtx_mod.is_released(tr.config.year, validation)
            fname, ctype = f"shinkoku_{year}_v{ver.version_no}.xtx", "application/xml"
        elif fmt == "pdf_forms":
            if tr.unsupported_items:
                raise ApiError("unsupported", "未対応の項目があるため出力できません。", status=409, details=tr.unsupported_items)
            content, validation, released = pdf_mod.return_forms_pdf(tr), {"ok": True, "layout": "転記用（独自レイアウト）"}, True
            fname, ctype = f"forms_{year}_v{ver.version_no}.pdf", "application/pdf"
        else:
            content, validation, released = xlsx_mod.return_xlsx(tr, build_guide(tr)), {"ok": True}, True
            fname, ctype = f"return_{year}_v{ver.version_no}.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        digest = hashlib.sha256(content).hexdigest()
        exp = TaxExport(version=ver, format=fmt, filename=fname, sha256=digest, size=len(content), generated_by=request.user, validation=validation, is_released=released)
        exp.file.save(f"t{tr.id}-{fmt}-{digest[:12]}.{fname.rsplit('.', 1)[-1]}", ContentFile(content), save=False)
        exp.save()
        if tr.status == TaxReturnStatus.COMPLETE:
            tr.status = TaxReturnStatus.EXPORTED
            tr.save(update_fields=["status", "updated_at"])
        record_tax_audit(tr.taxpayer, "export", user=request.user, tax_return=tr, detail={"format": fmt, "sha256": digest, "version_no": ver.version_no, "released": released})
        audit(request, "tax.export", target=exp, changes={"format": fmt, "sha256": digest})
        return Response({"id": exp.id, "format": fmt, "filename": fname, "sha256": digest, "size": exp.size, "validation": validation, "is_released": released,
                         "content_type": ctype}, status=201)

    def get(self, request, year):
        tr = get_return(request, int(year), create=False)
        rows = TaxExport.objects.filter(version__tax_return=tr).select_related("version")
        return Response([{"id": e.id, "format": e.format, "filename": e.filename, "sha256": e.sha256, "size": e.size, "generated_at": e.created_at,
                          "version_no": e.version.version_no, "is_released": e.is_released, "validation": e.validation, "download_count": e.download_count} for e in rows])


class ExportFileView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_TAX_RETURN_EXPORT, reauth=True)]

    def get(self, request, year, export_id):
        tr = get_return(request, int(year), create=False)
        exp = TaxExport.objects.filter(id=export_id, version__tax_return=tr).first()
        if exp is None:
            raise ApiError("not_found", "出力が見つかりません。", status=404)
        if not exp.is_released and not request.user.is_platform_operator:
            raise ApiError("xtx_not_released", "この出力は検証が完了するまで公開していません。", status=409)
        exp.download_count += 1
        exp.last_downloaded_at = timezone.now()
        exp.save(update_fields=["download_count", "last_downloaded_at", "updated_at"])
        record_tax_audit(tr.taxpayer, "download", user=request.user, tax_return=tr, detail={"export_id": exp.id, "sha256": exp.sha256})
        audit(request, "tax.export.download", target=exp)
        resp = FileResponse(exp.file.open("rb"), as_attachment=True, filename=exp.filename)
        resp["X-Content-Type-Options"] = "nosniff"
        return resp


class TaxAuditView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_TAX_RETURN_VIEW)]

    def get(self, request):
        tp = get_taxpayer(request)
        rows = TaxAuditLog.objects.filter(taxpayer=tp).select_related("user")[:300]
        return Response({"chain_ok": verify_chain(tp), "rows": [{"id": r.id, "action": r.action, "detail": r.detail, "user": str(r.user) if r.user else "",
                                                                  "created_at": r.created_at, "tax_return_id": r.tax_return_id} for r in rows]})
