"""
領収書ワークフローの業務ロジック（登録 → AI 読取 → 確認 → 承認 → 会計への反映）、買掛金、仕訳の差し込み。
views は薄く、判断はここに置く。
"""
from __future__ import annotations

import io
from datetime import date
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from django.utils.dateparse import parse_date

from apps.accounting import books, journals
from apps.accounting.books import TAX_CATEGORIES
from apps.accounting.compensation import taxpayer_for
from apps.accounting.models import Account, Evidence, Expense
from apps.accounting.serializers import validate_tax_breakdown
from apps.core import jobs
from apps.core.exceptions import ApiError
from apps.core.models import CompanySettings
from apps.core.money import D, ZERO

from . import duplicates
from .models import ExpenseReceipt, ExpenseReceiptEvent, OcrStatus, Payable, PayablePayment, ReceiptPaymentMethod, ReceiptStatus
from .ocr.providers import PROMPT_VERSION, get_provider, server_provider_name
from .ocr.schema import OcrResult, normalize_invoice_number

EDITABLE_STATUSES = (ReceiptStatus.DRAFT, ReceiptStatus.PENDING, ReceiptStatus.RETURNED)
APPROVABLE_STATUSES = (ReceiptStatus.PENDING,)
EXPENSE_PAID_FROM = {"owner_personal": "owner_personal", "bank": "bank", "card": "card"}


# --- 共通 ---------------------------------------------------------------------------------
def snapshot(r: ExpenseReceipt) -> dict:
    return {
        "status": r.status, "payment_method": r.payment_method, "receipt_date": r.receipt_date.isoformat() if r.receipt_date else None, "vendor": r.vendor,
        "total_amount": str(r.total_amount) if r.total_amount is not None else None, "tax_breakdown": r.tax_breakdown, "invoice_number": r.invoice_number,
        "summary": r.summary, "memo": r.memo, "approved_date": r.approved_date.isoformat() if r.approved_date else None, "approved_vendor": r.approved_vendor,
        "approved_amount": str(r.approved_amount) if r.approved_amount is not None else None, "account": r.account.key if r.account_id else None,
        "approved_tax_category": r.approved_tax_category, "approved_store": r.approved_store_id, "approved_payment_method": r.approved_payment_method,
    }


def event(r: ExpenseReceipt, action: str, actor, *, before: dict | None = None, after: dict | None = None, reason: str = "") -> ExpenseReceiptEvent:
    return ExpenseReceiptEvent.objects.create(receipt=r, action=action, actor=actor if getattr(actor, "is_authenticated", False) else None,
                                              before=before or {}, after=after or {}, reason=(reason or "")[:300])


def _diff(before: dict, after: dict) -> tuple[dict, dict]:
    b = {k: v for k, v in before.items() if after.get(k) != v}
    a = {k: v for k, v in after.items() if before.get(k) != v}
    return b, a


def account_candidates(receipt: ExpenseReceipt) -> list[dict]:
    """AI に渡す勘定科目の候補（費用・売上原価の有効な科目。名前と key だけ。個人情報は含まない）。"""
    tp = receipt.taxpayer or taxpayer_for(receipt.store)
    if tp is None:
        return [{"key": a[0], "name": a[2]} for a in books.STANDARD_ACCOUNTS if a[4] in ("expense", "cogs")]
    accounts = books.ensure_accounts(tp)
    return [{"key": a.key, "name": a.name} for a in accounts.values() if a.category in ("expense", "cogs") and a.is_active]


def image_for_ai(data: bytes, content_type: str) -> bytes:
    """AI へ送る画像: 長辺を縮小し JPEG に再エンコード（EXIF を落とす）。PDF はそのまま。"""
    if content_type == "application/pdf":
        return data
    try:
        from PIL import Image, ImageOps

        if content_type in ("image/heic", "image/heif"):
            try:
                import pillow_heif

                pillow_heif.register_heif_opener()
            except ImportError:
                return data
        img = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
        max_edge = int(getattr(settings, "RECEIPT_AI_MAX_EDGE", 2000))
        long_ = max(img.width, img.height)
        if long_ > max_edge:
            scale = max_edge / long_
            img = img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))))
        out = io.BytesIO()
        img.save(out, format="JPEG", quality=85, optimize=True)
        return out.getvalue()
    except Exception:  # noqa: BLE001
        return data


def ai_month_spent(company) -> Decimal:
    today = timezone.localdate()
    return ExpenseReceipt.objects.filter(company=company, created_at__year=today.year, created_at__month=today.month).aggregate(s=Sum("ocr_cost_jpy"))["s"] or ZERO


def ai_availability(company) -> dict:
    cs = CompanySettings.objects.filter(company=company).first()
    server = server_provider_name()
    spent = ai_month_spent(company)
    budget = cs.ai_monthly_budget_jpy if cs else Decimal("3000")
    enabled = bool(cs and cs.ai_enabled)
    provider = (cs.ai_provider if cs and cs.ai_provider else server) if enabled else ""
    reason = ""
    if not enabled:
        reason = "会社設定で AI 読取がオフです（手入力）"
    elif provider in ("none", "off", ""):
        reason = "サーバーに AI の接続設定がありません（手入力）"
    elif provider == "anthropic" and (not getattr(settings, "ANTHROPIC_API_KEY", "") or server != "anthropic"):
        reason = "サーバーに AI の API キーが設定されていません（手入力）"
    elif budget and budget > 0 and spent >= budget:
        reason = "今月の AI の予算を使い切りました（手入力）"
    return {"enabled": enabled, "provider": provider, "server_provider": server, "available": enabled and not reason, "reason": reason,
            "budget_jpy": str(budget), "spent_jpy": str(spent), "model": getattr(settings, "RECEIPT_AI_MODEL", "")}


# --- 登録 ---------------------------------------------------------------------------------
@transaction.atomic
def create_receipt(request, *, file, payment_method: str, memo: str = "", origin: str = "", captured_at=None, values: dict | None = None,
                   store=None, client_key: str = "") -> ExpenseReceipt:
    from apps.accounting.uploads import inspect_upload
    from apps.accounting.views import create_evidence

    store = store or request.store
    if payment_method not in ReceiptPaymentMethod.values:
        raise ApiError("validation_error", "支払方法を選んでください。")
    st = getattr(store, "settings", None)
    if st is not None and not getattr(st, "receipt_capture_enabled", True):
        raise ApiError("disabled", "この店舗では領収書の登録を使えません（店舗設定）。", status=409)
    info = inspect_upload(file)
    file.seek(0)
    tp = taxpayer_for(store)
    values = values or {}
    rdate = parse_date(values.get("receipt_date") or "") if values.get("receipt_date") else None
    amount = D(values["total_amount"]) if values.get("total_amount") not in (None, "") else None
    ev = create_evidence(request, file, info, kind="receipt", taxpayer=tp, transaction_date=rdate or timezone.localdate(), amount=amount,
                         counterparty=values.get("vendor") or "", description=(memo or "")[:200], origin=origin, captured_at=captured_at, store=store)
    r = ExpenseReceipt.objects.create(
        company=store.company, store=store, taxpayer=tp, evidence=ev, status=ReceiptStatus.DRAFT, payment_method=payment_method,
        submitted_by=request.user, memo=(memo or "")[:200], receipt_date=rdate, vendor=(values.get("vendor") or "")[:100], total_amount=amount,
        summary=(values.get("summary") or "")[:100], client_key=client_key or "",
    )
    r.duplicate_candidates = duplicates.find_candidates(r)
    r.save(update_fields=["duplicate_candidates", "updated_at"])
    event(r, "create", request.user, after=snapshot(r))
    start_ocr(r, request.user)
    return r


def start_ocr(r: ExpenseReceipt, user) -> None:
    avail = ai_availability(r.company)
    if not avail["available"]:
        mark_ocr_skipped(r, avail["reason"])
        return
    job = jobs.enqueue("expenses.ocr", {"receipt_id": r.id}, company=r.company, created_by=user if getattr(user, "is_authenticated", False) else None)
    r.ocr_status = OcrStatus.RUNNING
    r.status = ReceiptStatus.OCR_RUNNING
    r.ocr_job = job
    r.ocr_error = ""
    r.save(update_fields=["ocr_status", "status", "ocr_job", "ocr_error", "updated_at"])


def mark_ocr_skipped(r: ExpenseReceipt, reason: str) -> None:
    r.ocr_status = OcrStatus.SKIPPED
    r.ocr_error = (reason or "")[:300]
    if r.status in (ReceiptStatus.DRAFT, ReceiptStatus.OCR_RUNNING):
        r.status = ReceiptStatus.PENDING if r.submitted_at else ReceiptStatus.DRAFT
    r.save(update_fields=["ocr_status", "ocr_error", "status", "updated_at"])
    event(r, "ocr_skipped", None, reason=reason)


def mark_ocr_failed(r: ExpenseReceipt, reason: str) -> None:
    r.ocr_status = OcrStatus.FAILED
    r.ocr_error = (reason or "")[:300]
    if r.status == ReceiptStatus.OCR_RUNNING:
        r.status = ReceiptStatus.PENDING if r.submitted_at else ReceiptStatus.DRAFT
    r.save(update_fields=["ocr_status", "ocr_error", "status", "updated_at"])
    event(r, "ocr_failed", None, reason=reason)


def apply_ocr_result(r: ExpenseReceipt, res: OcrResult) -> None:
    r.ocr_status = OcrStatus.DONE
    r.ocr_provider, r.ocr_model, r.ocr_prompt_version = res.provider, res.model, res.prompt_version or PROMPT_VERSION
    r.ocr_raw = res.raw
    r.ocr_date = parse_date(res.date) if res.date else None
    r.ocr_vendor = res.vendor[:100]
    r.ocr_total = res.total
    r.ocr_tax_breakdown = res.tax_breakdown
    r.ocr_invoice_number = res.invoice_number
    r.ocr_summary = res.summary[:100]
    r.ocr_suggested_account_key = res.suggested_accounts[0]["key"] if res.suggested_accounts else ""
    r.ocr_confidence = {"missing_fields": res.missing_fields, "warnings": res.warnings, "suggested_accounts": res.suggested_accounts, "is_receipt": res.is_receipt,
                        "receipt_count": res.receipt_count}
    r.ocr_cost_jpy = res.cost_jpy
    r.ocr_error = ""
    # スタッフの確認値が空のところだけ AI の値を入れる（人が直してから登録する）
    if r.receipt_date is None and r.ocr_date:
        r.receipt_date = r.ocr_date
    if not r.vendor and r.ocr_vendor:
        r.vendor = r.ocr_vendor
    if r.total_amount is None and r.ocr_total is not None:
        r.total_amount = r.ocr_total
    if not r.tax_breakdown and r.ocr_tax_breakdown:
        r.tax_breakdown = r.ocr_tax_breakdown
    if not r.invoice_number and r.ocr_invoice_number:
        r.invoice_number = r.ocr_invoice_number
    if not r.summary and r.ocr_summary:
        r.summary = r.ocr_summary
    if r.status == ReceiptStatus.OCR_RUNNING:
        r.status = ReceiptStatus.PENDING if r.submitted_at else ReceiptStatus.DRAFT
    r.duplicate_candidates = duplicates.find_candidates(r)
    r.save()
    _sync_evidence(r)
    event(r, "ocr_done", None, after={"provider": res.provider, "model": res.model, "total": str(res.total) if res.total is not None else None,
                                      "warnings": res.warnings})


def retry_ocr(r: ExpenseReceipt, user) -> None:
    if r.status not in (*EDITABLE_STATUSES, ReceiptStatus.OCR_RUNNING):
        raise ApiError("invalid_status", "この状態では読み直せません。", status=409)
    avail = ai_availability(r.company)
    if not avail["available"]:
        raise ApiError("ai_unavailable", avail["reason"] or "AI 読取は使えません。", status=409)
    start_ocr(r, user)


def _sync_evidence(r: ExpenseReceipt) -> None:
    """電子帳簿保存法の検索項目（取引日・金額・取引先）を証憑に写す。"""
    ev = r.evidence
    changed = []
    d, a, v = r.effective_date, r.effective_amount, r.effective_vendor
    if d and ev.transaction_date != d:
        ev.transaction_date = d
        changed.append("transaction_date")
    if a is not None and ev.amount != a:
        ev.amount = a
        changed.append("amount")
    if v and ev.counterparty != v[:100]:
        ev.counterparty = v[:100]
        changed.append("counterparty")
    if r.taxpayer_id and ev.taxpayer_id != r.taxpayer_id:
        ev.taxpayer_id = r.taxpayer_id
        changed.append("taxpayer")
    if changed:
        ev.save(update_fields=changed + ["updated_at"])


def _check_version(r: ExpenseReceipt, version) -> None:
    if version is not None and str(version) != str(r.version):
        raise ApiError("version_conflict", "ほかの人が先に変更しました。画面を更新してやり直してください。", status=409)


@transaction.atomic
def update_values(r: ExpenseReceipt, user, data: dict, *, version=None) -> ExpenseReceipt:
    r = ExpenseReceipt.objects.select_for_update().get(pk=r.pk)
    if r.status not in EDITABLE_STATUSES:
        raise ApiError("invalid_status", "この状態では編集できません。", status=409)
    _check_version(r, version)
    before = snapshot(r)
    if "receipt_date" in data:
        d = parse_date(data["receipt_date"] or "") if data["receipt_date"] else None
        if data["receipt_date"] and d is None:
            raise ApiError("validation_error", "日付の形式が正しくありません。", details={"receipt_date": "YYYY-MM-DD"})
        if d and d > timezone.localdate():
            raise ApiError("validation_error", "未来の日付は登録できません。", details={"receipt_date": "未来日"})
        r.receipt_date = d
    if "vendor" in data:
        r.vendor = str(data["vendor"] or "")[:100]
    if "total_amount" in data:
        if data["total_amount"] in (None, ""):
            r.total_amount = None
        else:
            amt = D(data["total_amount"])
            if amt <= 0:
                raise ApiError("validation_error", "金額は1円以上で入力してください。", details={"total_amount": "1円以上"})
            r.total_amount = amt
    if "tax_breakdown" in data:
        from rest_framework import serializers as drf

        try:
            r.tax_breakdown = validate_tax_breakdown(data["tax_breakdown"])
        except drf.ValidationError as e:
            raise ApiError("validation_error", "税率内訳が正しくありません。", details={"tax_breakdown": e.detail})
    if "invoice_number" in data:
        raw = data["invoice_number"] or ""
        norm = normalize_invoice_number(raw)
        if raw and not norm:
            raise ApiError("validation_error", "登録番号は T＋13桁の数字です。", details={"invoice_number": "T+13桁"})
        r.invoice_number = norm
    if "summary" in data:
        r.summary = str(data["summary"] or "")[:100]
    if "memo" in data:
        r.memo = str(data["memo"] or "")[:200]
    if "payment_method" in data:
        if data["payment_method"] not in ReceiptPaymentMethod.values:
            raise ApiError("validation_error", "支払方法を選んでください。", details={"payment_method": "選択"})
        r.payment_method = data["payment_method"]
    r.version += 1
    r.duplicate_candidates = duplicates.find_candidates(r)
    r.save()
    _sync_evidence(r)
    b, a = _diff(before, snapshot(r))
    event(r, "edit", user, before=b, after=a)
    return r


@transaction.atomic
def submit(r: ExpenseReceipt, user, *, version=None) -> ExpenseReceipt:
    r = ExpenseReceipt.objects.select_for_update().get(pk=r.pk)
    if r.status not in (ReceiptStatus.DRAFT, ReceiptStatus.RETURNED, ReceiptStatus.OCR_RUNNING):
        raise ApiError("invalid_status", "この状態では登録できません。", status=409)
    _check_version(r, version)
    if r.total_amount is None and r.ocr_status != OcrStatus.RUNNING:
        raise ApiError("validation_error", "金額を入力してください。", details={"total_amount": "必須"})
    if r.receipt_date is None and r.ocr_status != OcrStatus.RUNNING:
        raise ApiError("validation_error", "日付を入力してください。", details={"receipt_date": "必須"})
    was_returned = r.status == ReceiptStatus.RETURNED
    r.submitted_at = timezone.now()
    r.submitted_by = user
    if r.status != ReceiptStatus.OCR_RUNNING:
        r.status = ReceiptStatus.PENDING
    r.version += 1
    r.duplicate_candidates = duplicates.find_candidates(r)
    r.save()
    _sync_evidence(r)
    event(r, "resubmit" if was_returned else "submit", user, after=snapshot(r))
    return r


@transaction.atomic
def void(r: ExpenseReceipt, user, *, reason: str = "") -> ExpenseReceipt:
    r = ExpenseReceipt.objects.select_for_update().get(pk=r.pk)
    if r.status not in (*EDITABLE_STATUSES, ReceiptStatus.OCR_RUNNING):
        raise ApiError("invalid_status", "承認済みの領収書は取り消せません（訂正を使ってください）。", status=409)
    r.status = ReceiptStatus.VOIDED
    r.version += 1
    r.save(update_fields=["status", "version", "updated_at"])
    event(r, "void", user, reason=reason)
    return r


# --- 承認 ---------------------------------------------------------------------------------
def _rebuild_journals(tp, *dates) -> None:
    """承認・訂正・支払いの直後に、その日の仕訳を作り直す。
    帳簿は仕訳行から集計するので、「仕訳を作る」を押さなくても帳簿に出るようここで同期する（build は何度実行しても同じ結果）。"""
    for d in sorted({x for x in dates if x}):
        journals.build(tp, d, d)


def _account(tp, data: dict) -> Account:
    books.ensure_accounts(tp)
    acc = None
    if data.get("account") not in (None, ""):
        v = data["account"]
        acc = Account.objects.filter(taxpayer=tp, id=v).first() if str(v).isdigit() else Account.objects.filter(taxpayer=tp, key=v).first()
    elif data.get("account_key"):
        acc = Account.objects.filter(taxpayer=tp, key=data["account_key"]).first()
    if acc is None:
        raise ApiError("validation_error", "勘定科目を選んでください。", details={"account": "必須"})
    if acc.category not in ("expense", "cogs"):
        raise ApiError("validation_error", "経費・仕入の科目を選んでください。", details={"account": "費用・売上原価の科目"})
    if not acc.is_active:
        raise ApiError("validation_error", "無効な科目です。", details={"account": "無効"})
    return acc


def _store_of_taxpayer(tp, request_store, store_id):
    from apps.core.models import Store

    if store_id in (None, ""):
        return request_store
    s = Store.objects.filter(id=store_id, company=tp.company).first()
    if s is None or taxpayer_for(s) != tp:
        raise ApiError("validation_error", "この事業者の店舗を選んでください。", details={"store": "事業者の店舗"})
    return s


def _main_rate(breakdown: list[dict], fallback: Decimal = Decimal(10)) -> Decimal:
    if not breakdown:
        return fallback
    top = max(breakdown, key=lambda x: D(x.get("taxable_incl") or 0))
    return D(top.get("rate") or fallback)


@transaction.atomic
def approve(r: ExpenseReceipt, user, data: dict, *, request_store=None) -> ExpenseReceipt:
    r = ExpenseReceipt.objects.select_for_update().select_related("evidence", "store").get(pk=r.pk)
    if r.status not in APPROVABLE_STATUSES:
        raise ApiError("invalid_status", "確認待ちの領収書だけ承認できます。", status=409)
    _check_version(r, data.get("version"))
    tp = r.taxpayer or taxpayer_for(r.store)
    if tp is None:
        raise ApiError("taxpayer_required", "この店舗の事業者が未設定です。「会計設定」で事業者を設定してください。", status=409)
    books.ensure_accounts(tp)
    before = snapshot(r)
    d = parse_date(data.get("date") or "") if data.get("date") else (r.receipt_date or r.ocr_date)
    if d is None:
        raise ApiError("validation_error", "日付を入力してください。", details={"date": "必須"})
    if d > timezone.localdate():
        raise ApiError("validation_error", "未来の日付は承認できません。", details={"date": "未来日"})
    amount = D(data["amount"]) if data.get("amount") not in (None, "") else r.effective_amount
    if amount is None or amount <= 0:
        raise ApiError("validation_error", "金額を入力してください。", details={"amount": "1円以上"})
    vendor = str(data.get("vendor") if data.get("vendor") is not None else r.effective_vendor or "")[:100]
    pm = data.get("payment_method") or r.payment_method
    if pm not in ReceiptPaymentMethod.values or pm == ReceiptPaymentMethod.OTHER:
        raise ApiError("validation_error", "支払方法を「店舗現金／オーナー個人資金／銀行振込／クレジットカード」のどれかに決めてください。", details={"payment_method": "その他は承認不可"})
    acc = _account(tp, data)
    tax_category = data.get("tax_category") or acc.default_tax or "purchase_10"
    if tax_category not in TAX_CATEGORIES:
        raise ApiError("validation_error", "税区分が不正です。", details={"tax_category": "不正"})
    store = _store_of_taxpayer(tp, r.store, data.get("store"))
    from rest_framework import serializers as drf

    try:
        breakdown = validate_tax_breakdown(data["tax_breakdown"]) if "tax_breakdown" in data else list(r.tax_breakdown or [])
    except drf.ValidationError as e:
        raise ApiError("validation_error", "税率内訳が正しくありません。", details={"tax_breakdown": e.detail})
    if breakdown and sum(D(x["taxable_incl"]) for x in breakdown) > amount:
        raise ApiError("validation_error", "税率内訳の合計が金額を超えています。", details={"tax_breakdown": "合計超過"})
    if books.locked_year(tp, d) is not None:
        raise ApiError("year_locked", f"{books.year_of(tp, d)}年度は締め済みです。再開してから承認してください。", status=409)
    # 重複候補があるときは理由なしに承認しない
    r.duplicate_candidates = duplicates.find_candidates(r)
    override = (data.get("duplicate_override_reason") or "").strip()
    if r.duplicate_candidates and not override:
        raise ApiError("duplicate_suspected", "同じ領収書の可能性があります。別の取引なら理由を入れて承認してください。", status=409,
                       details={"candidates": r.duplicate_candidates})
    invoice = normalize_invoice_number(data.get("invoice_number")) if data.get("invoice_number") is not None else r.invoice_number
    description = (data.get("summary") if data.get("summary") is not None else (r.summary or r.memo) or "")[:200]

    if pm == ReceiptPaymentMethod.STORE_CASH:
        _approve_store_cash(r, user, store=store, acc=acc, amount=amount, d=d, vendor=vendor, description=description, data=data, tp=tp,
                            breakdown=breakdown, tax_category=tax_category, invoice=invoice)
    elif data.get("as_payable"):
        p = create_payable(tp, user, {"store": store.id, "counterparty": vendor, "invoice_date": d.isoformat(), "due_date": data.get("due_date"),
                                      "amount": str(amount), "tax_breakdown": breakdown, "account": acc.id, "description": description,
                                      "evidence": r.evidence_id, "source": "receipt"})
        r.payable = p
    else:
        exp = Expense.objects.create(
            company=tp.company, taxpayer=tp, store=store, date=d, amount=amount, tax_rate=_main_rate(breakdown), account_key=acc.key, account=acc,
            tax_breakdown=breakdown, counterparty=vendor, invoice_number=invoice, description=description, paid_from=EXPENSE_PAID_FROM[pm],
            evidence=r.evidence, created_by=user, corrected_from=data.get("_corrected_from"),
        )
        r.expense = exp
    r.approved_date, r.approved_vendor, r.approved_amount = d, vendor, amount
    r.approved_tax_breakdown, r.account, r.approved_tax_category = breakdown, acc, tax_category
    r.approved_store, r.approved_payment_method = store, pm
    r.approved_note = (data.get("note") or "")[:200]
    r.duplicate_override_reason = override[:200]
    r.invoice_number = invoice
    r.taxpayer = tp
    r.status = ReceiptStatus.APPROVED
    r.reviewed_by, r.reviewed_at = user, timezone.now()
    r.version += 1
    r.save()
    _sync_evidence(r)
    b, a = _diff(before, snapshot(r))
    event(r, data.get("_event") or "approve", user, before=b, after=a, reason=override)
    _rebuild_journals(tp, d, r.cash_movement.business_day.date if r.cash_movement_id else None)
    return r


def _approve_store_cash(r, user, *, store, acc, amount, d, vendor, description, data, tp, breakdown, tax_category, invoice):
    from apps.cash.models import BusinessDay, CashMovement

    mv_id = data.get("cash_movement_id")
    if mv_id:
        mv = CashMovement.objects.select_for_update().filter(id=mv_id, store=store, kind="expense", is_voided=False).first()
        if mv is None:
            raise ApiError("validation_error", "結び付けるレジ出金が見つかりません。", details={"cash_movement_id": "見つからない"})
        if getattr(mv, "expense_receipt", None) is not None and mv.expense_receipt.id != r.id:
            raise ApiError("validation_error", "そのレジ出金には別の領収書が結び付いています。", details={"cash_movement_id": "使用済み"})
        if mv.amount != amount:
            raise ApiError("validation_error", f"レジ出金の金額 {mv.amount:,.0f}円 と承認金額が違います。", details={"amount": str(mv.amount)})
        mv.account = acc
        mv.save(update_fields=["account", "updated_at"])
        r.cash_movement = mv
        return
    day = BusinessDay.objects.filter(store=store, date=d).first()
    if day is None or day.status != "open":
        mode = data.get("closed_day_mode")
        if mode == "office_cash":
            exp = Expense.objects.create(
                company=tp.company, taxpayer=tp, store=store, date=d, amount=amount, tax_rate=_main_rate(breakdown), account_key=acc.key, account=acc,
                tax_breakdown=breakdown, counterparty=vendor, invoice_number=invoice, description=description, paid_from="office_cash", evidence=r.evidence,
                created_by=user, corrected_from=data.get("_corrected_from"),
            )
            r.expense = exp
            return
        raise ApiError(
            "business_day_closed" if day is not None else "business_day_missing",
            f"{d.isoformat()} の営業日は{'締め済み' if day is not None else '開店記録がありません'}。「締め後の修正」として営業日を再開してレジ出金を登録するか、"
            "レジ以外の手許現金払いの経費として登録してください。",
            status=409,
            details={"date": d.isoformat(), "business_day_status": day.status if day else None, "options": ["office_cash", "reopen_day"]},
        )
    mv = CashMovement.objects.create(
        store=store, business_day=day, kind="expense", amount=amount, reason=(f"{vendor} {description}".strip() or "領収書")[:200],
        occurred_at=timezone.now(), performed_by=user, account=acc,
    )
    r.cash_movement = mv


@transaction.atomic
def return_receipt(r: ExpenseReceipt, user, *, reason: str, version=None) -> ExpenseReceipt:
    r = ExpenseReceipt.objects.select_for_update().get(pk=r.pk)
    if r.status not in APPROVABLE_STATUSES:
        raise ApiError("invalid_status", "確認待ちの領収書だけ差し戻せます。", status=409)
    _check_version(r, version)
    if not (reason or "").strip():
        raise ApiError("validation_error", "差戻しの理由を入力してください。", details={"reason": "必須"})
    r.status = ReceiptStatus.RETURNED
    r.return_reason = reason[:200]
    r.reviewed_by, r.reviewed_at = user, timezone.now()
    r.version += 1
    r.save()
    event(r, "return", user, reason=reason)
    return r


@transaction.atomic
def reject(r: ExpenseReceipt, user, *, reason: str, version=None) -> ExpenseReceipt:
    r = ExpenseReceipt.objects.select_for_update().get(pk=r.pk)
    if r.status not in (ReceiptStatus.PENDING, ReceiptStatus.RETURNED, ReceiptStatus.DRAFT):
        raise ApiError("invalid_status", "この状態では却下できません。", status=409)
    _check_version(r, version)
    if not (reason or "").strip():
        raise ApiError("validation_error", "却下の理由を入力してください。", details={"reason": "必須"})
    r.status = ReceiptStatus.REJECTED
    r.reject_reason = reason[:200]
    r.reviewed_by, r.reviewed_at = user, timezone.now()
    r.version += 1
    r.save()
    event(r, "reject", user, reason=reason)
    return r


@transaction.atomic
def correct(r: ExpenseReceipt, user, data: dict) -> ExpenseReceipt:
    """承認後の訂正: 会計への反映を取り消してから、新しい内容で承認し直す（履歴は events と Expense.corrected_from に残る）。"""
    r = ExpenseReceipt.objects.select_for_update(of=("self",)).select_related("expense", "cash_movement", "payable").get(pk=r.pk)
    if r.status != ReceiptStatus.APPROVED:
        raise ApiError("invalid_status", "承認済みの領収書だけ訂正できます。", status=409)
    _check_version(r, data.get("version"))
    reason = (data.get("reason") or "").strip()
    if not reason:
        raise ApiError("validation_error", "訂正の理由を入力してください。", details={"reason": "必須"})
    tp = r.taxpayer
    if r.approved_date and books.locked_year(tp, r.approved_date) is not None:
        raise ApiError("year_locked", "締め済み年度の承認は訂正できません。", status=409)
    old_expense = None
    old_dates = []
    if r.cash_movement_id:
        mv = r.cash_movement
        if mv.business_day.status != "open":
            raise ApiError("day_closed", "レジ締め済みの営業日の出金は取り消せません。営業日を再開してから訂正してください。", status=409)
        mv.is_voided, mv.void_reason, mv.voided_by = True, f"領収書の訂正: {reason}"[:200], user
        mv.save()
        old_dates.append(mv.business_day.date)
        r.cash_movement = None
    if r.expense_id:
        old_expense = r.expense
        old_expense.is_voided, old_expense.void_reason = True, f"領収書の訂正: {reason}"[:200]
        old_expense.save(update_fields=["is_voided", "void_reason", "updated_at"])
        old_dates.append(old_expense.date)
        r.expense = None
    if r.payable_id:
        p = r.payable
        if p.payments.filter(is_voided=False).exists():
            raise ApiError("payable_paid", "支払い済みの買掛金に結び付いています。先に支払いを取り消してください。", status=409)
        p.is_voided, p.void_reason = True, f"領収書の訂正: {reason}"[:200]
        p.refresh_status()
        p.save()
        old_dates.append(p.invoice_date)
        r.payable = None
    before = snapshot(r)
    r.status = ReceiptStatus.PENDING
    r.save()
    payload = {**data, "version": r.version, "_event": "correct", "_corrected_from": old_expense}
    payload.setdefault("duplicate_override_reason", r.duplicate_override_reason or "訂正")
    r2 = approve(r, user, payload)
    _rebuild_journals(tp, *old_dates)
    ev = r2.events.order_by("-id").first()
    if ev is not None:
        ev.before = before
        ev.reason = reason[:300]
        ev.save(update_fields=["before", "reason"])
    return r2


# --- 買掛金 ---------------------------------------------------------------------------------
def _liability_key(acc: Account | None) -> str:
    return "accounts_payable" if acc is not None and acc.category == "cogs" else "accrued_card"


@transaction.atomic
def create_payable(tp, user, data: dict) -> Payable:
    from apps.core.models import Store

    amount = D(data.get("amount") or 0)
    if amount <= 0:
        raise ApiError("validation_error", "金額は1円以上で入力してください。", details={"amount": "1円以上"})
    d = parse_date(data.get("invoice_date") or "")
    if d is None:
        raise ApiError("validation_error", "請求日（取引日）を入力してください。", details={"invoice_date": "必須"})
    acc = _account(tp, data)
    store = None
    if data.get("store"):
        store = Store.objects.filter(id=data["store"], company=tp.company).first()
        if store is None:
            raise ApiError("validation_error", "店舗が見つかりません。", details={"store": "不正"})
    supplier = None
    if data.get("supplier"):
        from apps.inventory.models import Supplier

        supplier = Supplier.objects.filter(id=data["supplier"], store__company=tp.company).first()
    from rest_framework import serializers as drf

    try:
        breakdown = validate_tax_breakdown(data.get("tax_breakdown"))
    except drf.ValidationError as e:
        raise ApiError("validation_error", "税率内訳が正しくありません。", details={"tax_breakdown": e.detail})
    evidence = Evidence.objects.filter(id=data["evidence"], company=tp.company).first() if data.get("evidence") else None
    p = Payable.objects.create(
        company=tp.company, taxpayer=tp, store=store, supplier=supplier, counterparty=(data.get("counterparty") or (supplier.name if supplier else ""))[:100],
        invoice_date=d, due_date=parse_date(data.get("due_date") or "") if data.get("due_date") else None, amount=amount, tax_rate=_main_rate(breakdown),
        tax_breakdown=breakdown, account=acc, description=(data.get("description") or "")[:200], evidence=evidence, source=data.get("source") or "manual",
        created_by=user,
    )
    return p


@transaction.atomic
def add_payment(p: Payable, user, data: dict, *, request_store=None) -> PayablePayment:
    p = Payable.objects.select_for_update().get(pk=p.pk)
    if p.is_voided:
        raise ApiError("voided", "取消済みの買掛金です。", status=409)
    amount = D(data.get("amount") or 0)
    if amount <= 0:
        raise ApiError("validation_error", "支払額は1円以上で入力してください。", details={"amount": "1円以上"})
    if amount > p.balance:
        raise ApiError("validation_error", f"残高 {p.balance:,.0f}円 を超えています。", details={"amount": str(p.balance)})
    d = parse_date(data.get("date") or "")
    if d is None:
        raise ApiError("validation_error", "支払日を入力してください。", details={"date": "必須"})
    method = data.get("method") or "bank"
    if method not in ("bank", "cash_register", "owner_personal", "card"):
        raise ApiError("validation_error", "支払方法を選んでください。", details={"method": "不正"})
    if books.locked_year(p.taxpayer, d) is not None:
        raise ApiError("year_locked", "締め済み年度には支払いを登録できません。", status=409)
    mv = None
    if method == "cash_register":
        from apps.cash.models import CashMovement
        from apps.cash.services import require_open_business_day

        store = p.store or request_store
        if store is None or taxpayer_for(store) != p.taxpayer:
            raise ApiError("validation_error", "この事業者の店舗のレジを選んでください。", details={"store": "事業者の店舗"})
        day = require_open_business_day(store)
        liab = books.account_for(p.taxpayer, _liability_key(p.account))
        mv = CashMovement.objects.create(store=store, business_day=day, kind="expense", amount=amount, reason=f"買掛金の支払い {p.counterparty}"[:200],
                                         occurred_at=timezone.now(), performed_by=user, account=liab)
    pay = PayablePayment.objects.create(payable=p, date=d, amount=amount, method=method, cash_movement=mv, note=(data.get("note") or "")[:200], created_by=user)
    p.paid_amount += amount
    p.refresh_status()
    p.save(update_fields=["paid_amount", "status", "updated_at"])
    _rebuild_journals(p.taxpayer, d, mv.business_day.date if mv is not None else None)
    return pay


@transaction.atomic
def void_payment(pay: PayablePayment, user, *, reason: str) -> PayablePayment:
    pay = PayablePayment.objects.select_for_update(of=("self",)).select_related("payable", "cash_movement").get(pk=pay.pk)
    if pay.is_voided:
        raise ApiError("voided", "すでに取消済みです。", status=409)
    if not (reason or "").strip():
        raise ApiError("validation_error", "取消理由を入力してください。", details={"reason": "必須"})
    if pay.cash_movement_id:
        mv = pay.cash_movement
        if mv.business_day.status != "open":
            raise ApiError("day_closed", "レジ締め済みの営業日の出金は取り消せません。", status=409)
        mv.is_voided, mv.void_reason, mv.voided_by = True, reason[:200], user
        mv.save()
    pay.is_voided, pay.void_reason = True, reason[:200]
    pay.save(update_fields=["is_voided", "void_reason", "updated_at"])
    p = pay.payable
    p.paid_amount -= pay.amount
    p.refresh_status()
    p.save(update_fields=["paid_amount", "status", "updated_at"])
    _rebuild_journals(p.taxpayer, pay.date, pay.cash_movement.business_day.date if pay.cash_movement_id else None)
    return pay


@transaction.atomic
def void_payable(p: Payable, user, *, reason: str) -> Payable:
    p = Payable.objects.select_for_update().get(pk=p.pk)
    if p.is_voided:
        raise ApiError("voided", "すでに取消済みです。", status=409)
    if not (reason or "").strip():
        raise ApiError("validation_error", "取消理由を入力してください。", details={"reason": "必須"})
    if p.payments.filter(is_voided=False).exists():
        raise ApiError("payable_paid", "支払いが登録されています。先に支払いを取り消してください。", status=409)
    p.is_voided, p.void_reason = True, reason[:200]
    p.refresh_status()
    p.save()
    _rebuild_journals(p.taxpayer, p.invoice_date)
    return p


def build_hook(taxpayer, start: date, end: date) -> int:
    """journals.build からの差し込み: 買掛金の発生と支払い（レジ現金以外）を仕訳にする。"""
    count = 0
    for p in Payable.objects.filter(taxpayer=taxpayer, invoice_date__gte=start, invoice_date__lte=end).select_related("account", "store"):
        key = p.account.key if p.account_id else "misc_expense"
        liab = _liability_key(p.account)
        lines = []
        if p.tax_breakdown:
            allocated = ZERO
            for row in p.tax_breakdown:
                amt = D(row.get("taxable_incl") or 0)
                if amt > 0:
                    lines.append(journals._line("debit", key, amt, journals._purchase_tax(row.get("rate")), p.description, counterparty=p.counterparty))
                    allocated += amt
            if p.amount - allocated:
                lines.append(journals._line("debit", key, p.amount - allocated, journals._purchase_tax(p.tax_rate), p.description, counterparty=p.counterparty))
        else:
            lines.append(journals._line("debit", key, p.amount, journals._purchase_tax(p.tax_rate), p.description, counterparty=p.counterparty))
        lines.append(journals._line("credit", liab, p.amount, memo=f"{p.counterparty} 未払", counterparty=p.counterparty))
        journals.upsert(taxpayer, store=p.store, source_type="payable", source_id=p.id, issue_date=p.invoice_date,
                        description=f"{'買掛金' if liab == 'accounts_payable' else '未払金'} {p.counterparty} {p.description}".strip(), lines=lines, void=p.is_voided)
        count += 1
    pays = PayablePayment.objects.filter(payable__taxpayer=taxpayer, date__gte=start, date__lte=end, cash_movement__isnull=True).select_related("payable__account", "payable__store")
    credit_map = {"bank": "bank", "owner_personal": "owner_in", "card": "accrued_card"}
    for pay in pays:
        p = pay.payable
        liab = _liability_key(p.account)
        lines = [journals._line("debit", liab, pay.amount, memo=f"{p.counterparty} 支払", counterparty=p.counterparty),
                 journals._line("credit", credit_map.get(pay.method, "bank"), pay.amount, counterparty=p.counterparty)]
        journals.upsert(taxpayer, store=p.store, source_type="payable_payment", source_id=pay.id, issue_date=pay.date,
                        description=f"{p.counterparty} への支払い", lines=lines, void=pay.is_voided or p.is_voided)
        count += 1
    return count


# --- 一覧の絞り込み ------------------------------------------------------------------------------
def visible_receipts(request, *, approver: bool):
    """承認者・経理は事業者の店舗の全件、スタッフは自店舗（店長は店舗の全件、スタッフは自分の登録）。"""
    qs = ExpenseReceipt.objects.select_related("evidence", "store", "account", "approved_store", "submitted_by", "reviewed_by")
    if approver:
        tp = taxpayer_for(request.store)
        from django.db.models import Q

        qs = qs.filter(company=request.store.company)
        if tp is not None:
            qs = qs.filter(Q(taxpayer=tp) | Q(taxpayer__isnull=True, store__tax_profile__taxpayer=tp) | Q(taxpayer__isnull=True, store__tax_profile__isnull=True))
        return qs
    qs = qs.filter(store=request.store)
    if request.store_access.role not in ("manager", "owner", "admin", "operator", "bookkeeper"):
        qs = qs.filter(submitted_by=request.user)
    return qs
