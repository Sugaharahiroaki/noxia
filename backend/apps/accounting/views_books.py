"""帳簿基盤の API: 勘定科目・会計年度・帳簿（元帳など）・手入力仕訳。"""
from __future__ import annotations

from django.db import transaction
from django.utils.dateparse import parse_date
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.audit import audit
from apps.core.exceptions import ApiError
from apps.core.idempotency import idempotent
from apps.core.models import Store
from apps.core.permissions import CAP_ACCOUNTING_VIEW, CAP_BOOKS_ADJUST, CAP_TAX_VIEW, CAP_YEAREND_CONFIRM, CAP_YEAREND_PREPARE, StoreCaps

from . import books, exports, journals, ledgers
from .models import Account, AccountCategory, FiscalYear, Journal
from .serializers import JournalSerializer
from .views import date_range, get_taxpayer, require_any

LEDGER_KINDS = ("general", "cash", "bank", "sales", "receivables", "payables", "purchases", "expenses", "staff_payments", "trial_balance")


# --- 勘定科目 -----------------------------------------------------------------------------
class AccountsView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_ACCOUNTING_VIEW, write_caps=(CAP_BOOKS_ADJUST,))]

    def get(self, request):
        tp = get_taxpayer(request)
        accounts = books.ensure_accounts(tp)
        rows = [books.account_payload(a) for a in sorted(accounts.values(), key=lambda a: (a.sort_order, a.code, a.id))]
        if request.query_params.get("active") == "1":
            rows = [r for r in rows if r["is_active"]]
        return Response({"accounts": rows, "statement_lines": books.STATEMENT_LINE_LABELS, "tax_categories": books.TAX_CATEGORIES,
                         "categories": dict(AccountCategory.choices)})

    @transaction.atomic
    def post(self, request):
        tp = get_taxpayer(request)
        key = (request.data.get("key") or "").strip().lower()
        name = (request.data.get("name") or "").strip()
        category = request.data.get("category") or "expense"
        if not key or not key.replace("_", "").isalnum() or len(key) > 40:
            raise ApiError("validation_error", "科目キーは英数字とアンダースコアで入力してください。")
        if not name:
            raise ApiError("validation_error", "科目名を入力してください。")
        if category not in AccountCategory.values:
            raise ApiError("validation_error", "科目の区分を選んでください。")
        if Account.objects.filter(taxpayer=tp, key=key).exists():
            raise ApiError("duplicate", "同じキーの科目があります。", status=409)
        line = request.data.get("statement_line") or ("custom" if category in ("expense", "cogs") else "")
        if line and line not in books.STATEMENT_LINE_LABELS:
            raise ApiError("validation_error", "帳票の行が不正です。")
        tax = request.data.get("default_tax") or "out_of_scope"
        if tax not in books.TAX_CATEGORIES:
            raise ApiError("validation_error", "税区分が不正です。")
        a = Account.objects.create(taxpayer=tp, key=key, code=(request.data.get("code") or "")[:8], name=name[:60], category=category, statement_line=line,
                                   default_tax=tax, is_system=False, sort_order=int(request.data.get("sort_order") or 5000), note=(request.data.get("note") or "")[:200])
        audit(request, "accounting.account.create", target=a, changes={"key": key, "name": name})
        return Response(books.account_payload(a), status=201)


class AccountDetailView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_BOOKS_ADJUST)]

    @transaction.atomic
    def patch(self, request, account_id):
        tp = get_taxpayer(request)
        a = Account.objects.filter(id=account_id, taxpayer=tp).first()
        if a is None:
            raise ApiError("not_found", "科目が見つかりません。", status=404)
        before = books.account_payload(a)
        for f in ("name", "code", "note"):
            if f in request.data:
                setattr(a, f, str(request.data[f] or "")[: {"name": 60, "code": 8, "note": 200}[f]])
        if "statement_line" in request.data:
            line = request.data["statement_line"] or ""
            if line and line not in books.STATEMENT_LINE_LABELS:
                raise ApiError("validation_error", "帳票の行が不正です。")
            a.statement_line = line
        if "default_tax" in request.data:
            if request.data["default_tax"] not in books.TAX_CATEGORIES:
                raise ApiError("validation_error", "税区分が不正です。")
            a.default_tax = request.data["default_tax"]
        if "is_active" in request.data:
            if a.is_system and not request.data["is_active"]:
                raise ApiError("system_account", "標準科目は無効にできません。", status=409)
            a.is_active = bool(request.data["is_active"])
        if "sort_order" in request.data:
            a.sort_order = int(request.data["sort_order"] or 0)
        a.save()
        audit(request, "accounting.account.update", target=a, changes={"before": before, "after": books.account_payload(a)})
        return Response(books.account_payload(a))


# --- 会計年度 -----------------------------------------------------------------------------
class FiscalYearsView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_ACCOUNTING_VIEW, write_caps=(CAP_YEAREND_PREPARE,))]

    def get(self, request):
        tp = get_taxpayer(request)
        return Response([books.year_payload(fy) for fy in FiscalYear.objects.filter(taxpayer=tp).prefetch_related("events")])

    @transaction.atomic
    def post(self, request):
        tp = get_taxpayer(request)
        try:
            year = int(request.data.get("year"))
        except (TypeError, ValueError):
            raise ApiError("validation_error", "年度を指定してください。")
        if not 2000 <= year <= 2100:
            raise ApiError("validation_error", "年度が範囲外です。")
        fy = books.get_fiscal_year(tp, year, create=True)
        if request.data.get("filing_type") in ("white", "blue_10", "blue_55", "blue_65"):
            fy.filing_type = request.data["filing_type"]
            fy.save(update_fields=["filing_type", "updated_at"])
        audit(request, "accounting.fiscal_year.create", target=fy, changes={"year": year})
        return Response(books.year_payload(fy), status=201)


class FiscalYearDetailView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_ACCOUNTING_VIEW)]

    def get(self, request, year):
        tp = get_taxpayer(request)
        fy = books.get_fiscal_year(tp, int(year))
        if fy is None:
            raise ApiError("not_found", "年度が見つかりません。", status=404)
        data = books.year_payload(fy)
        data["checklist"] = year_checklist(tp, fy)
        return Response(data)


def year_checklist(tp, fy: FiscalYear) -> list[dict]:
    """年度を締める前の確認事項（締めていない営業日・仮勘定・未承認の領収書・未照合の明細）。"""
    from apps.cash.models import BusinessDay

    from .models import StoreTaxProfile

    store_ids = list(StoreTaxProfile.objects.filter(taxpayer=tp).values_list("store_id", flat=True))
    items = []
    open_days = BusinessDay.objects.filter(store_id__in=store_ids, status="open", date__gte=fy.start_date, date__lte=fy.end_date).count()
    items.append({"code": "open_days", "label": "締めていない営業日", "count": open_days, "blocking": True})
    suspense = journals.Journal.objects.filter(taxpayer=tp, is_void=False, issue_date__gte=fy.start_date, issue_date__lte=fy.end_date,
                                               entry_lines__account__key__startswith="suspense").distinct().count()
    items.append({"code": "suspense", "label": "仮勘定（仮払金・仮受金）が残る仕訳", "count": suspense, "blocking": True})
    try:
        from apps.expenses.models import ExpenseReceipt

        pending = ExpenseReceipt.objects.filter(taxpayer=tp, status__in=["pending", "returned", "ocr_running"], receipt_date__gte=fy.start_date,
                                                receipt_date__lte=fy.end_date).count()
        items.append({"code": "pending_receipts", "label": "確認待ち・差戻し中の領収書", "count": pending, "blocking": True})
    except Exception:  # noqa: BLE001
        pass
    try:
        from apps.banking.models import BankTransaction

        unmatched = BankTransaction.objects.filter(bank_account__taxpayer=tp, status__in=["unmatched", "suggested"], date__gte=fy.start_date,
                                                   date__lte=fy.end_date).count()
        items.append({"code": "unmatched_bank", "label": "未照合の銀行明細", "count": unmatched, "blocking": False})
    except Exception:  # noqa: BLE001
        pass
    try:
        from apps.closing.models import InventoryClosing

        has_closing = InventoryClosing.objects.filter(fiscal_year=fy, kind="closing", status="confirmed").exists()
        items.append({"code": "inventory_closing", "label": "期末棚卸が未確定", "count": 0 if has_closing else 1, "blocking": False})
    except Exception:  # noqa: BLE001
        pass
    items.append({"code": "drift", "label": "締め後に元データが変わった記録", "count": len(fy.drift_warnings or []), "blocking": False})
    return items


class FiscalYearActionView(APIView):
    """年度の締め・再開（経営者本人・明示の再認証）。"""

    def get_permissions(self):
        return [StoreCaps(CAP_YEAREND_CONFIRM, reauth=True)]

    @idempotent
    def post(self, request, year, action):
        tp = get_taxpayer(request)
        fy = books.get_fiscal_year(tp, int(year), create=(action == "close"))
        if fy is None:
            raise ApiError("not_found", "年度が見つかりません。", status=404)
        reason = (request.data.get("reason") or "").strip()
        if action == "close":
            blocking = [c for c in year_checklist(tp, fy) if c["blocking"] and c["count"]]
            if blocking and not request.data.get("force"):
                raise ApiError("checklist", "締める前に確認が必要な項目があります。", status=409, details=blocking)
            fy = books.close_year(fy, request.user, reason=reason)
        elif action == "reopen":
            fy = books.reopen_year(fy, request.user, reason=reason)
        elif action == "file":
            from django.utils import timezone

            if not fy.is_locked:
                raise ApiError("not_closed", "年度を締めてから「申告済み」にしてください。", status=409)
            fy.status = "filed"
            fy.filed_at = timezone.now()
            fy.save(update_fields=["status", "filed_at", "updated_at"])
            books.FiscalYearEvent.objects.create(fiscal_year=fy, action="file", reason=reason[:300], performed_by=request.user)
        else:
            raise ApiError("not_found", "操作が見つかりません。", status=404)
        audit(request, f"accounting.fiscal_year.{action}", target=fy, changes={"year": fy.year, "reason": reason})
        return Response(books.year_payload(fy))


# --- 帳簿 ----------------------------------------------------------------------------------
def _ledger_data(request, kind: str) -> dict:
    tp = get_taxpayer(request)
    f, t = date_range(request)
    p = request.query_params
    store_id = int(p["store"]) if p.get("store") else None
    if store_id and not Store.objects.filter(id=store_id, company=request.store.company).exists():
        raise ApiError("not_found", "店舗が見つかりません。", status=404)
    if kind == "general":
        if not p.get("account"):
            raise ApiError("validation_error", "科目を指定してください。")
        return ledgers.general_ledger(tp, p["account"], f, t, store_id=store_id, bank_account_ref=int(p["bank_account"]) if p.get("bank_account") else None)
    if kind == "cash":
        return ledgers.cash_book(tp, f, t, store_id=store_id)
    if kind == "bank":
        return ledgers.bank_book(tp, f, t, bank_account_ref=int(p["bank_account"]) if p.get("bank_account") else None)
    if kind == "sales":
        return ledgers.sales_book(tp, f, t, store_id=store_id, group="month" if p.get("group") == "month" else "day")
    if kind == "receivables":
        return ledgers.receivables(tp, f, t)
    if kind == "payables":
        return ledgers.payables(tp, f, t)
    if kind == "purchases":
        return ledgers.expense_book(tp, f, t, account_key=p.get("account"), store_id=store_id, categories=("cogs",))
    if kind == "expenses":
        return ledgers.expense_book(tp, f, t, account_key=p.get("account"), store_id=store_id, categories=("expense",))
    if kind == "staff_payments":
        require_any(request, CAP_TAX_VIEW, CAP_ACCOUNTING_VIEW)
        return ledgers.staff_payments_book(tp, f, t, employee_id=int(p["employee"]) if p.get("employee") else None)
    if kind == "trial_balance":
        return ledgers.trial_balance(tp, f, t)
    raise ApiError("not_found", "帳簿の種類が見つかりません。", status=404)


class LedgerView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_ACCOUNTING_VIEW)]

    def get(self, request, name):
        kind, _, ext = name.partition(".")
        if kind not in LEDGER_KINDS:
            raise ApiError("not_found", "帳簿の種類が見つかりません。", status=404)
        data = _ledger_data(request, kind)
        if ext in ("csv", "xlsx"):
            title, header, rows = ledgers.ledger_table(kind, data)
            fname = f"{kind}_{data.get('from', '')}_{data.get('to', '')}.{ext}"
            if ext == "csv":
                return exports.csv_response(request, fname, header, rows, f"export.ledger.{kind}", encoding="cp932" if request.query_params.get("sjis") == "1" else "utf-8-sig")
            return exports.xlsx_response(request, fname, [(title, header, rows)], f"export.ledger.{kind}")
        if ext:
            raise ApiError("not_found", "出力形式が見つかりません。", status=404)
        return Response(data)


class LedgersBookView(APIView):
    """全帳簿を 1 つの XLSX に（帳簿ごとのシート）。"""

    def get_permissions(self):
        return [StoreCaps(CAP_ACCOUNTING_VIEW)]

    def get(self, request):
        tp = get_taxpayer(request)
        f, t = date_range(request)
        sheets = []
        for kind in ("sales", "cash", "bank", "receivables", "payables", "purchases", "expenses", "staff_payments", "trial_balance"):
            data = _ledger_data(request, kind) if kind != "general" else None
            if data is not None:
                sheets.append(ledgers.ledger_table(kind, data))
        return exports.xlsx_response(request, f"ledgers_{f:%Y%m%d}_{t:%Y%m%d}.xlsx", sheets, "export.ledgers.book")


# --- 手入力仕訳 ------------------------------------------------------------------------------
class ManualJournalView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_BOOKS_ADJUST)]

    @idempotent
    def post(self, request):
        tp = get_taxpayer(request)
        d = parse_date(request.data.get("issue_date") or "")
        if d is None:
            raise ApiError("validation_error", "日付を入力してください。")
        store = None
        if request.data.get("store"):
            store = Store.objects.filter(id=request.data["store"], company=request.store.company).first()
            if store is None:
                raise ApiError("validation_error", "店舗が見つかりません。")
        kind = request.data.get("entry_kind") or "manual"
        if kind not in ("manual", "adjustment", "opening"):
            raise ApiError("validation_error", "仕訳の種類は manual / adjustment / opening から選んでください。")
        j = books.create_manual_journal(tp, request.user, issue_date=d, description=request.data.get("description") or "", lines=request.data.get("lines") or [],
                                        entry_kind=kind, store=store)
        audit(request, "accounting.journal.manual", target=j, changes={"issue_date": d, "lines": j.lines})
        return Response(JournalSerializer(j).data, status=201)


class JournalDetailView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_ACCOUNTING_VIEW, write_caps=(CAP_BOOKS_ADJUST,))]

    def _get(self, request) -> Journal:
        tp = get_taxpayer(request)
        j = Journal.objects.filter(id=self.kwargs["journal_id"], taxpayer=tp).select_related("freee_sync", "store").first()
        if j is None:
            raise ApiError("not_found", "仕訳が見つかりません。", status=404)
        return j

    def get(self, request, journal_id):
        j = self._get(request)
        data = JournalSerializer(j).data
        data["entry_lines"] = [
            {"line_no": ln.line_no, "side": ln.side, "account_key": ln.account.key, "account_name": ln.account.name, "amount": str(ln.amount),
             "tax_category": ln.tax_category, "tax_amount": str(ln.tax_amount) if ln.tax_amount is not None else None, "store": ln.store.name if ln.store else "",
             "counterparty": ln.counterparty, "employee": ln.employee.display_name if ln.employee else "", "memo": ln.memo}
            for ln in j.entry_lines.select_related("account", "store", "employee")
        ]
        data["fiscal_year"] = books.year_payload(j.fiscal_year) if j.fiscal_year else None
        return Response(data)

    def patch(self, request, journal_id):
        j = self._get(request)
        d = parse_date(request.data.get("issue_date") or "") if request.data.get("issue_date") else None
        j = books.update_manual_journal(j, request.user, issue_date=d, description=request.data.get("description"), lines=request.data.get("lines"))
        audit(request, "accounting.journal.update", target=j, changes={"lines": j.lines})
        return Response(JournalSerializer(j).data)


class JournalVoidView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_BOOKS_ADJUST)]

    def post(self, request, journal_id):
        tp = get_taxpayer(request)
        j = Journal.objects.filter(id=journal_id, taxpayer=tp).first()
        if j is None:
            raise ApiError("not_found", "仕訳が見つかりません。", status=404)
        j = books.void_manual_journal(j, request.user, reason=request.data.get("reason") or "")
        audit(request, "accounting.journal.void", target=j, changes={"reason": request.data.get("reason")})
        return Response(JournalSerializer(j).data)
