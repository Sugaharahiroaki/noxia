"""年間決算 API（/api/closing/…）。閲覧は accounting.view、作成・計算は yearend.prepare。年度の締めは accounting の fiscal-years API。"""
from __future__ import annotations

from django.db import transaction
from django.utils.dateparse import parse_date
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounting import books, exports
from apps.accounting.models import Evidence
from apps.accounting.views import get_taxpayer, pdf_response
from apps.accounting.views_books import year_checklist
from apps.core.audit import audit
from apps.core.exceptions import ApiError
from apps.core.idempotency import idempotent
from apps.core.models import Store
from apps.core.money import D
from apps.core.permissions import CAP_ACCOUNTING_VIEW, CAP_YEAREND_PREPARE, StoreCaps

from . import adjustments, depreciation, inventory, reports
from .models import ClosingAdjustment, DepreciationEntry, DepreciationMethod, FixedAsset, FixedAssetKind, FixedAssetStatus, InventoryClosing


def _perm():
    return [StoreCaps(CAP_ACCOUNTING_VIEW, write_caps=(CAP_YEAREND_PREPARE,))]


def _year(request, default_from_query: bool = True) -> int:
    raw = request.query_params.get("year") if default_from_query else None
    if raw is None and isinstance(getattr(request, "data", None), dict):
        raw = request.data.get("year")
    try:
        y = int(raw)
    except (TypeError, ValueError):
        raise ApiError("validation_error", "年度（year）を指定してください。")
    if not 2000 <= y <= 2100:
        raise ApiError("validation_error", "年度が範囲外です。")
    return y


def _store(request, store_id, required: bool = False):
    if not store_id:
        if required:
            raise ApiError("validation_error", "店舗を選んでください。")
        return None
    st = Store.objects.filter(id=store_id, company=request.store.company).first()
    if st is None:
        raise ApiError("not_found", "店舗が見つかりません。", status=404)
    return st


# --- チェックリスト ----------------------------------------------------------------------------
class YearChecklistView(APIView):
    def get_permissions(self):
        return _perm()

    def get(self, request, year):
        tp = get_taxpayer(request)
        fy = books.get_fiscal_year(tp, int(year), create=True)
        items = year_checklist(tp, fy)
        n_assets = FixedAsset.objects.filter(taxpayer=tp, acquired_on__lte=fy.end_date).exclude(status__in=["disposed", "sold"], disposed_on__lt=fy.start_date).count()
        n_dep = DepreciationEntry.objects.filter(fiscal_year=fy).count()
        items.append({"code": "depreciation", "label": "減価償却の計算が未実行（対象資産あり）", "count": 1 if n_assets and n_dep == 0 else 0, "blocking": False})
        opening = InventoryClosing.objects.filter(fiscal_year=fy, kind="opening", status="confirmed").exists()
        prev_closing = InventoryClosing.objects.filter(taxpayer=tp, fiscal_year__year=fy.year - 1, kind="closing", status="confirmed").exists()
        items.append({"code": "inventory_opening", "label": "期首棚卸が未確定（前年の期末棚卸があります）", "count": 1 if prev_closing and not opening else 0, "blocking": False})
        return Response({"year": fy.year, "fiscal_year": books.year_payload(fy), "items": items, "methods": depreciation.method_options(fy.filing_type)})


# --- 棚卸 ----------------------------------------------------------------------------------------
class InventoryView(APIView):
    def get_permissions(self):
        return _perm()

    def get(self, request):
        tp = get_taxpayer(request)
        qs = InventoryClosing.objects.filter(taxpayer=tp).select_related("fiscal_year", "store").prefetch_related("lines")
        if request.query_params.get("year"):
            qs = qs.filter(fiscal_year__year=_year(request))
        if request.query_params.get("store"):
            qs = qs.filter(store_id=request.query_params["store"])
        if request.query_params.get("kind"):
            qs = qs.filter(kind=request.query_params["kind"])
        from apps.inventory.models import StockTake

        takes = StockTake.objects.filter(store__company=request.store.company, store__tax_profile__taxpayer=tp).select_related("store").order_by("-taken_at")[:50]
        return Response({"closings": [inventory.payload(c, with_lines=False) for c in qs],
                         "stock_takes": [{"id": t.id, "store": t.store_id, "store_name": t.store.name, "taken_at": t.taken_at, "status": t.status, "lines": t.lines.count()} for t in takes]})

    @idempotent
    def post(self, request):
        tp = get_taxpayer(request)
        year = _year(request, default_from_query=False)
        store = _store(request, request.data.get("store"), required=True)
        stock_take = None
        if request.data.get("stock_take"):
            from apps.inventory.models import StockTake

            stock_take = StockTake.objects.filter(id=request.data["stock_take"], store=store).first()
            if stock_take is None:
                raise ApiError("not_found", "棚卸し結果が見つかりません。", status=404)
        if request.data.get("from_previous"):
            c = inventory.opening_from_previous(tp, request.user, year=year, store=store)
        else:
            c = inventory.create_draft(tp, request.user, year=year, store=store, kind=request.data.get("kind") or "closing", stock_take=stock_take,
                                       lines=request.data.get("lines"), as_of=parse_date(request.data.get("as_of") or "") or None,
                                       method=request.data.get("method") or "last_purchase_cost", note=request.data.get("note") or "")
        audit(request, "closing.inventory.create", target=c, changes={"year": year, "kind": c.kind, "total": c.total_amount})
        return Response(inventory.payload(c), status=201)


class InventoryDetailView(APIView):
    def get_permissions(self):
        return _perm()

    def _get(self, request, closing_id) -> InventoryClosing:
        c = InventoryClosing.objects.filter(id=closing_id, taxpayer=get_taxpayer(request)).select_related("fiscal_year", "store").first()
        if c is None:
            raise ApiError("not_found", "棚卸が見つかりません。", status=404)
        return c

    def get(self, request, closing_id):
        return Response(inventory.payload(self._get(request, closing_id)))

    @transaction.atomic
    def delete(self, request, closing_id):
        c = self._get(request, closing_id)
        if c.status == "confirmed":
            raise ApiError("already_confirmed", "確定済みの棚卸は削除できません。先に確定を取り消してください。", status=409)
        audit(request, "closing.inventory.delete", target=c)
        c.delete()
        return Response(status=204)


class InventoryActionView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_YEAREND_PREPARE)]

    @idempotent
    def post(self, request, closing_id, action):
        c = InventoryClosing.objects.filter(id=closing_id, taxpayer=get_taxpayer(request)).select_related("fiscal_year", "store").first()
        if c is None:
            raise ApiError("not_found", "棚卸が見つかりません。", status=404)
        if action == "confirm":
            c = inventory.confirm(c, request.user)
        elif action == "unconfirm":
            c = inventory.unconfirm(c, request.user, reason=request.data.get("reason") or "")
        else:
            raise ApiError("not_found", "操作が見つかりません。", status=404)
        audit(request, f"closing.inventory.{action}", target=c, changes={"total": c.total_amount, "journal_id": c.journal_id})
        return Response(inventory.payload(c))


# --- 固定資産 -------------------------------------------------------------------------------------
def _apply_asset_fields(request, a: FixedAsset, data: dict, *, creating: bool) -> None:
    if "name" in data or creating:
        a.name = (data.get("name") or "").strip()[:120]
        if not a.name:
            raise ApiError("validation_error", "資産名を入力してください。")
    if "kind" in data or creating:
        kind = data.get("kind") or "tangible"
        if kind not in FixedAssetKind.values:
            raise ApiError("validation_error", "資産の種類が不正です。")
        a.kind = kind
    if "category" in data:
        a.category = (data.get("category") or "")[:60]
    if "acquired_on" in data or creating:
        d = parse_date(str(data.get("acquired_on") or ""))
        if d is None:
            raise ApiError("validation_error", "取得日を入力してください。")
        a.acquired_on = d
    if "acquisition_cost" in data or creating:
        a.acquisition_cost = D(data.get("acquisition_cost") or 0)
    if "business_use_percent" in data:
        a.business_use_percent = D(data.get("business_use_percent") or 100)
    if "depreciation_method" in data or creating:
        m = data.get("depreciation_method") or "straight_line"
        if m not in DepreciationMethod.values:
            raise ApiError("validation_error", "償却方法が不正です。")
        a.depreciation_method = m
    if "useful_life_years" in data:
        v = data.get("useful_life_years")
        a.useful_life_years = int(v) if v not in (None, "") else None
    if "rate" in data and data.get("rate") not in (None, ""):
        a.rate = D(data["rate"])
    elif a.depreciation_method == "straight_line" and ("useful_life_years" in data or creating):
        a.rate = depreciation.rate_for(a.useful_life_years)
    if "store" in data:
        a.store = _store(request, data.get("store"))
    if "paid_from" in data:
        if data["paid_from"] not in ("none", "bank", "cash", "owner_personal", "card"):
            raise ApiError("validation_error", "支払方法が不正です。")
        a.paid_from = data["paid_from"]
    if "evidence" in data:
        ev = Evidence.objects.filter(id=data["evidence"], company=request.store.company).first() if data.get("evidence") else None
        if data.get("evidence") and ev is None:
            raise ApiError("validation_error", "証憑が見つかりません。")
        a.evidence = ev
    if "note" in data:
        a.note = (data.get("note") or "")[:300]


class FixedAssetsView(APIView):
    def get_permissions(self):
        return _perm()

    def get(self, request):
        tp = get_taxpayer(request)
        qs = FixedAsset.objects.filter(taxpayer=tp).select_related("store").prefetch_related("entries__fiscal_year")
        if request.query_params.get("status"):
            qs = qs.filter(status=request.query_params["status"])
        fy = None
        if request.query_params.get("year"):
            fy = books.get_fiscal_year(tp, _year(request))
        return Response({"assets": [depreciation.asset_payload(a) for a in qs], "methods": depreciation.method_options(fy.filing_type if fy else "white"),
                         "rates": depreciation.rates_table()})

    @idempotent
    @transaction.atomic
    def post(self, request):
        tp = get_taxpayer(request)
        a = FixedAsset(taxpayer=tp, created_by=request.user)
        _apply_asset_fields(request, a, request.data, creating=True)
        fy = books.get_fiscal_year(tp, books.year_of(tp, a.acquired_on), create=True)
        if fy.is_locked:
            raise ApiError("year_locked", "取得日の年度は締め済みです。再開してから登録してください。", status=409)
        depreciation.validate_asset(a, fy)
        a.save()
        depreciation.record_acquisition(a, request.user)
        audit(request, "closing.asset.create", target=a, changes={"name": a.name, "cost": a.acquisition_cost, "method": a.depreciation_method})
        return Response(depreciation.asset_payload(a), status=201)


class FixedAssetDetailView(APIView):
    def get_permissions(self):
        return _perm()

    def _get(self, request, asset_id) -> FixedAsset:
        a = FixedAsset.objects.filter(id=asset_id, taxpayer=get_taxpayer(request)).select_related("store").first()
        if a is None:
            raise ApiError("not_found", "資産が見つかりません。", status=404)
        return a

    def get(self, request, asset_id):
        a = self._get(request, asset_id)
        data = depreciation.asset_payload(a)
        data["entries"] = [depreciation.entry_payload(e) for e in a.entries.select_related("fiscal_year", "asset__store").order_by("fiscal_year__year")]
        return Response(data)

    @transaction.atomic
    def patch(self, request, asset_id):
        a = self._get(request, asset_id)
        if DepreciationEntry.objects.filter(asset=a, fiscal_year__status__in=["closed", "filed"]).exists():
            raise ApiError("year_locked", "締め済みの年度で償却した資産は変更できません。", status=409)
        before = depreciation.asset_payload(a)
        _apply_asset_fields(request, a, request.data, creating=False)
        fy = books.get_fiscal_year(a.taxpayer, books.year_of(a.taxpayer, a.acquired_on), create=True)
        depreciation.validate_asset(a, fy)
        a.save()
        audit(request, "closing.asset.update", target=a, changes={"before": {k: str(v) for k, v in before.items() if k in ("name", "acquisition_cost", "depreciation_method", "useful_life_years")}, "after": request.data})
        return Response(depreciation.asset_payload(a))

    @transaction.atomic
    def delete(self, request, asset_id):
        a = self._get(request, asset_id)
        if a.entries.exists():
            raise ApiError("has_entries", "償却を計算した資産は削除できません。「除却」にしてください。", status=409)
        if a.acquisition_journal_id and not a.acquisition_journal.is_void:
            books.void_manual_journal(a.acquisition_journal, request.user, reason="資産の登録を取り消し")
        audit(request, "closing.asset.delete", target=a, changes={"name": a.name})
        a.delete()
        return Response(status=204)


class FixedAssetDisposeView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_YEAREND_PREPARE)]

    @idempotent
    @transaction.atomic
    def post(self, request, asset_id):
        a = FixedAsset.objects.filter(id=asset_id, taxpayer=get_taxpayer(request)).first()
        if a is None:
            raise ApiError("not_found", "資産が見つかりません。", status=404)
        d = parse_date(str(request.data.get("disposed_on") or ""))
        if d is None or d < a.acquired_on:
            raise ApiError("validation_error", "除却・売却の日を取得日以降で入力してください。")
        if books.locked_year(a.taxpayer, d) is not None:
            raise ApiError("year_locked", "その日の年度は締め済みです。", status=409)
        kind = request.data.get("kind") or "disposed"
        if kind not in ("disposed", "sold"):
            raise ApiError("validation_error", "除却か売却を選んでください。")
        a.status, a.disposed_on = kind, d
        a.disposal_amount = D(request.data["disposal_amount"]) if request.data.get("disposal_amount") not in (None, "") else None
        a.save()
        audit(request, "closing.asset.dispose", target=a, changes={"kind": kind, "date": d, "amount": a.disposal_amount})
        return Response({**depreciation.asset_payload(a), "notice": "除却・売却時の損益（固定資産除却損・譲渡所得など）は自動では仕訳しません。帳簿価額が残る場合は税理士に確認し、決算整理で登録してください。"})


# --- 減価償却 -------------------------------------------------------------------------------------
class DepreciationView(APIView):
    def get_permissions(self):
        return _perm()

    def get(self, request):
        tp = get_taxpayer(request)
        year = _year(request)
        fy = books.get_fiscal_year(tp, year)
        entries = DepreciationEntry.objects.filter(fiscal_year=fy).select_related("asset__store") if fy else []
        rows = [depreciation.entry_payload(e) for e in entries]
        return Response({"year": year, "entries": rows, "total_business": str(sum((D(r["amount_business"]) for r in rows), D(0))),
                         "locked": bool(fy and fy.is_locked)})


class DepreciationComputeView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_YEAREND_PREPARE)]

    @idempotent
    def post(self, request):
        tp = get_taxpayer(request)
        year = _year(request, default_from_query=False)
        res = depreciation.compute_year(tp, year, request.user, voluntary=request.data.get("voluntary") or {})
        audit(request, "closing.depreciation.compute", changes={"year": year, "total_business": res["total_business"], "count": len(res["entries"])})
        return Response(res)


# --- 決算整理 -------------------------------------------------------------------------------------
class AdjustmentsView(APIView):
    def get_permissions(self):
        return _perm()

    def get(self, request):
        tp = get_taxpayer(request)
        qs = ClosingAdjustment.objects.filter(taxpayer=tp).select_related("fiscal_year", "debit_account", "credit_account", "store")
        if request.query_params.get("year"):
            qs = qs.filter(fiscal_year__year=_year(request))
        if request.query_params.get("include_void") != "1":
            qs = qs.filter(is_voided=False)
        return Response({"adjustments": [adjustments.payload(a) for a in qs], "kinds": [{"key": k, "label": v, "help": adjustments.KIND_HELP.get(k, ""),
                                                                                       "default_debit": adjustments.DEFAULT_ACCOUNTS[k][0], "default_credit": adjustments.DEFAULT_ACCOUNTS[k][1]}
                                                                                      for k, v in ClosingAdjustment._meta.get_field("kind").choices]})

    @idempotent
    def post(self, request):
        tp = get_taxpayer(request)
        year = _year(request, default_from_query=False)
        adj = adjustments.create(tp, request.user, year=year, kind=request.data.get("kind") or "", description=request.data.get("description") or "",
                                 amount=request.data.get("amount") or 0, debit_key=request.data.get("debit_key") or "", credit_key=request.data.get("credit_key") or "",
                                 issue_date=parse_date(request.data.get("issue_date") or "") or None, store=_store(request, request.data.get("store")))
        audit(request, "closing.adjustment.create", target=adj, changes={"kind": adj.kind, "amount": adj.amount, "description": adj.description})
        return Response(adjustments.payload(adj), status=201)


class AdjustmentVoidView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_YEAREND_PREPARE)]

    def post(self, request, adjustment_id):
        adj = ClosingAdjustment.objects.filter(id=adjustment_id, taxpayer=get_taxpayer(request)).select_related("fiscal_year").first()
        if adj is None:
            raise ApiError("not_found", "決算整理が見つかりません。", status=404)
        adj = adjustments.void(adj, request.user, reason=request.data.get("reason") or "")
        audit(request, "closing.adjustment.void", target=adj, changes={"reason": adj.void_reason})
        return Response(adjustments.payload(adj))


# --- 年間損益 -------------------------------------------------------------------------------------
class YearPlView(APIView):
    def get_permissions(self):
        return _perm()

    def get(self, request, year, ext=""):
        tp = get_taxpayer(request)
        fy = books.get_fiscal_year(tp, int(year), create=True)
        store_id = int(request.query_params["store"]) if request.query_params.get("store") else None
        if store_id:
            _store(request, store_id, required=True)
        data = reports.year_pl(tp, fy, by=request.query_params.get("by") or "taxpayer", store_id=store_id)
        if ext == "xlsx":
            return exports.xlsx_response(request, f"year_pl_{fy.year}.xlsx", reports.pl_sheets(data), "export.year_pl.xlsx")
        if ext == "pdf":
            return pdf_response(request, f"year_pl_{fy.year}.pdf", reports.pl_pdf(tp, data), "export.year_pl.pdf")
        return Response(data)
