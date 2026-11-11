"""
減価償却の計算（tax_calculation_rules.md §5）。

- 定額法: 取得価額 × 償却率（表の値）× 使用月数/12。1円未満切捨て。帳簿価額は備忘価額（1円）まで。取得月を含めて月数を数える。
- 一括償却資産（20万円未満）: 取得価額 ÷ 3 を3年（月割なし、最後の年で端数を合わせる。備忘価額なし）。
- 少額（10万円未満）: 取得年度に全額を消耗品費。
- 30万円未満の特例（措法28の2）: 青色申告者のみ、年300万円まで。取得年度に全額。
- 任意償却（開業費など繰延資産）: 利用者が年度ごとに額を指定（残額まで）。備忘価額なし。
- 定率法: 届出が必要なため未対応（エラー）。
- 家事按分: 必要経費算入額 = 償却費 × 事業専用割合（1円未満切捨て）。残りは事業主貸。
"""
from __future__ import annotations

import json
from datetime import date
from decimal import ROUND_FLOOR, Decimal
from pathlib import Path

from django.db import transaction
from django.utils import timezone

from apps.accounting import books
from apps.accounting.models import FiscalYear, TaxPayer
from apps.core.exceptions import ApiError
from apps.core.money import D, ZERO

from .models import DepreciationEntry, DepreciationMethod, FixedAsset, FixedAssetStatus

RATES_PATH = Path(__file__).resolve().parent / "data" / "depreciation_rates.json"
_rates_cache: dict | None = None

IMMEDIATE_LIMIT = Decimal("100000")  # 未満
LUMP_SUM_LIMIT = Decimal("200000")  # 未満
BLUE_SMALL_LIMIT = Decimal("300000")  # 未満
BLUE_SMALL_ANNUAL_CAP = Decimal("3000000")


def rates_table() -> dict:
    global _rates_cache
    if _rates_cache is None:
        _rates_cache = json.loads(RATES_PATH.read_text(encoding="utf-8"))
    return _rates_cache


def rate_for(useful_life_years: int | None) -> Decimal:
    table = rates_table()["rates"]
    if useful_life_years is None or str(useful_life_years) not in table:
        raise ApiError("validation_error", "耐用年数は 2〜50 年の範囲で入力してください（国税庁の償却率表にある年数）。")
    return Decimal(table[str(useful_life_years)])


def _floor(v: Decimal) -> Decimal:
    return D(v).quantize(Decimal("1"), rounding=ROUND_FLOOR)


def months_in_service(asset: FixedAsset, fy: FiscalYear) -> int:
    """年度内の使用月数（取得月を含む。除却・売却の月まで。月未満は切上げ）。"""
    start, end = fy.start_date, fy.end_date
    if asset.acquired_on > end:
        return 0
    first = max(asset.acquired_on, start)
    last = end
    if asset.status != FixedAssetStatus.IN_USE and asset.disposed_on:
        if asset.disposed_on < start:
            return 0
        last = min(asset.disposed_on, end)
    months = (last.year - first.year) * 12 + (last.month - first.month) + 1
    return max(0, min(12, months))


def prior_total(asset: FixedAsset, year: int) -> Decimal:
    """year より前の年度の償却累計（未計算の年度があれば 0 として扱い、根拠に注記する）。"""
    total = ZERO
    for e in DepreciationEntry.objects.filter(asset=asset, fiscal_year__year__lt=year):
        total += e.amount_total
    return total


def validate_asset(asset: FixedAsset, fy: FiscalYear | None = None) -> None:
    """登録時・計算時の検証（方法と金額の整合）。"""
    cost = D(asset.acquisition_cost)
    if cost <= 0:
        raise ApiError("validation_error", "取得価額は1円以上で入力してください。")
    if not (0 < D(asset.business_use_percent) <= 100):
        raise ApiError("validation_error", "事業専用割合は 1〜100% で入力してください。")
    m = asset.depreciation_method
    if m == DepreciationMethod.DECLINING_BALANCE:
        raise ApiError("unsupported", "定率法は税務署への届出が必要なため、このシステムでは対応していません。定額法を選ぶか税理士にご相談ください。", status=400)
    if m == DepreciationMethod.IMMEDIATE and cost >= IMMEDIATE_LIMIT:
        raise ApiError("validation_error", "少額（全額経費）は取得価額 10万円未満のものだけです。")
    if m == DepreciationMethod.LUMP_SUM_3YR and cost >= LUMP_SUM_LIMIT:
        raise ApiError("validation_error", "一括償却資産は取得価額 20万円未満のものだけです。")
    if m == DepreciationMethod.BLUE_SMALL_ASSET:
        if cost >= BLUE_SMALL_LIMIT:
            raise ApiError("validation_error", "30万円未満の特例は取得価額 30万円未満のものだけです。")
        if fy is not None and not (fy.filing_type or "").startswith("blue"):
            raise ApiError("unsupported", "30万円未満の特例（少額減価償却資産）は青色申告者だけが使えます。白色申告の年度では定額法か一括償却を選んでください。", status=400)
    if m == DepreciationMethod.STRAIGHT_LINE:
        if asset.is_deferred:
            raise ApiError("validation_error", "開業費などの繰延資産は「任意償却」を選んでください。")
        if asset.rate is None:
            asset.rate = rate_for(asset.useful_life_years)
    if m == DepreciationMethod.VOLUNTARY and not asset.is_deferred:
        raise ApiError("validation_error", "任意償却は開業費などの繰延資産だけに使えます。")


def compute_asset_year(asset: FixedAsset, fy: FiscalYear, *, requested: Decimal | None = None, blue_used: Decimal = ZERO) -> dict:
    """1資産・1年度の償却額と根拠。DB は読むだけ。"""
    cost = D(asset.acquisition_cost)
    year = fy.year
    before = prior_total(asset, year)
    book_begin = cost - before
    memo = D(asset.memorandum_value) if not asset.is_deferred and asset.depreciation_method not in (DepreciationMethod.LUMP_SUM_3YR, DepreciationMethod.IMMEDIATE, DepreciationMethod.BLUE_SMALL_ASSET) else ZERO
    months = months_in_service(asset, fy)
    basis: dict = {"method": asset.depreciation_method, "cost": str(cost), "book_value_begin": str(book_begin), "months": months,
                   "business_use_percent": str(asset.business_use_percent), "prior_total": str(before)}
    amount = ZERO
    acquired_year = books.year_of(asset.taxpayer, asset.acquired_on)
    if asset.acquired_on > fy.end_date or months == 0:
        basis["note"] = "この年度では使用していません"
    elif asset.depreciation_method == DepreciationMethod.STRAIGHT_LINE:
        rate = D(asset.rate) if asset.rate is not None else rate_for(asset.useful_life_years)
        amount = _floor(cost * rate * Decimal(months) / Decimal(12))
        basis.update({"rate": str(rate), "formula": f"{cost:,.0f} × {rate} × {months}/12（1円未満切捨て）"})
        limit = book_begin - memo
        if amount > limit:
            amount = max(ZERO, limit)
            basis["capped"] = f"帳簿価額が備忘価額 {memo:,.0f}円 になるまで"
    elif asset.depreciation_method == DepreciationMethod.LUMP_SUM_3YR:
        n = year - acquired_year + 1
        if 1 <= n <= 3:
            amount = _floor(cost / Decimal(3)) if n < 3 else book_begin
            basis.update({"year_no": n, "formula": f"{cost:,.0f} ÷ 3（{n}年目。3年目は残額）"})
        else:
            basis["note"] = "一括償却の3年を過ぎています"
        amount = min(amount, book_begin)
    elif asset.depreciation_method == DepreciationMethod.IMMEDIATE:
        if year == acquired_year:
            amount = book_begin
            basis["formula"] = "取得年度に全額を消耗品費"
        else:
            basis["note"] = "取得年度に全額を経費にしています"
    elif asset.depreciation_method == DepreciationMethod.BLUE_SMALL_ASSET:
        if not (fy.filing_type or "").startswith("blue"):
            raise ApiError("unsupported", f"{asset.name}: 30万円未満の特例は青色申告者だけが使えます（{year}年度は白色）。", status=400)
        if year == acquired_year:
            if blue_used + cost > BLUE_SMALL_ANNUAL_CAP:
                raise ApiError("validation_error", f"{asset.name}: 30万円未満の特例は年間 300万円までです（この年度の合計が超えます）。")
            amount = book_begin
            basis["formula"] = "取得年度に全額を必要経費（措法28の2）"
        else:
            basis["note"] = "取得年度に全額を経費にしています"
    elif asset.depreciation_method == DepreciationMethod.VOLUNTARY:
        req = D(requested) if requested is not None else ZERO
        if req < 0:
            raise ApiError("validation_error", "任意償却の額は 0 以上で入力してください。")
        if req > book_begin:
            raise ApiError("validation_error", f"{asset.name}: 任意償却の額が残額 {book_begin:,.0f}円 を超えています。")
        amount = _floor(req)
        basis["formula"] = "任意償却（利用者が指定した額。残額まで）"
    else:
        raise ApiError("unsupported", "この償却方法は対応していません。", status=400)
    pct = D(asset.business_use_percent)
    business = _floor(amount * pct / Decimal(100))
    household = amount - business
    return {"amount_total": amount, "amount_business": business, "amount_household": household, "book_value_begin": book_begin,
            "book_value_end": book_begin - amount, "months": months, "basis": basis}


@transaction.atomic
def compute_year(taxpayer: TaxPayer, year: int, user, *, voluntary: dict | None = None) -> dict:
    """年度の償却をすべての資産について計算し直す（同じ結果なら仕訳は変わらない）。締めた年度は 409。"""
    fy = books.get_fiscal_year(taxpayer, year, create=True)
    if fy.is_locked:
        raise ApiError("year_locked", f"{year}年度は締め済みのため、減価償却を計算し直せません。再開してから行ってください。", status=409)
    voluntary = {int(k): D(v) for k, v in (voluntary or {}).items()}
    assets = list(FixedAsset.objects.filter(taxpayer=taxpayer, acquired_on__lte=fy.end_date).select_related("store").order_by("acquired_on", "id"))
    results = []
    blue_used = ZERO
    seen_ids = set()
    for asset in assets:
        existing = DepreciationEntry.objects.filter(asset=asset, fiscal_year=fy).first()
        requested = voluntary.get(asset.id)
        if requested is None and existing is not None and existing.requested_amount is not None:
            requested = existing.requested_amount
        res = compute_asset_year(asset, fy, requested=requested, blue_used=blue_used)
        if asset.depreciation_method == DepreciationMethod.BLUE_SMALL_ASSET and res["amount_total"] > 0:
            blue_used += res["amount_total"]
        entry = existing or DepreciationEntry(asset=asset, fiscal_year=fy)
        entry.months_in_service = res["months"]
        entry.amount_total, entry.amount_business, entry.amount_household = res["amount_total"], res["amount_business"], res["amount_household"]
        entry.book_value_begin, entry.book_value_end = res["book_value_begin"], res["book_value_end"]
        entry.requested_amount = requested if asset.depreciation_method == DepreciationMethod.VOLUNTARY else None
        entry.calc_basis = res["basis"]
        source_id = f"{asset.id}:{year}"
        if res["amount_total"] > 0:
            lines = [{"side": "debit", "key": asset.expense_account_key, "amount": str(res["amount_business"]), "memo": f"{asset.name} 減価償却"[:100]}]
            if res["amount_household"] > 0:
                lines.append({"side": "debit", "key": "owner_out", "amount": str(res["amount_household"]), "memo": f"{asset.name} 家事分"[:100]})
            lines = [ln for ln in lines if D(ln["amount"]) > 0]
            lines.append({"side": "credit", "key": asset.asset_account_key, "amount": str(res["amount_total"]), "memo": asset.name[:100]})
            j = books.create_manual_journal(taxpayer, user, issue_date=fy.end_date, description=f"減価償却 {year}年度 {asset.name}"[:200], lines=lines,
                                            entry_kind="adjustment", store=asset.store, source_type="depreciation", source_id=source_id)
            entry.journal = j
        else:
            if entry.journal_id and not entry.journal.is_void:
                books.void_manual_journal(entry.journal, user, reason="償却額が 0 になったため")
            entry.journal = None if not entry.journal_id else entry.journal
        entry.save()
        seen_ids.add(asset.id)
        results.append(entry_payload(entry))
    # 資産が消えた・対象外になった行は取り消す
    for stale in DepreciationEntry.objects.filter(fiscal_year=fy).exclude(asset_id__in=seen_ids):
        if stale.journal_id and not stale.journal.is_void:
            books.void_manual_journal(stale.journal, user, reason="対象の資産がなくなったため")
        stale.delete()
    total = sum((D(r["amount_business"]) for r in results), ZERO)
    return {"year": year, "entries": results, "total_business": str(total), "blue_small_used": str(blue_used)}


def entry_payload(e: DepreciationEntry) -> dict:
    a = e.asset
    return {
        "id": e.id, "asset_id": a.id, "asset_name": a.name, "category": a.category, "kind": a.kind, "method": a.depreciation_method,
        "method_label": a.get_depreciation_method_display(), "acquired_on": a.acquired_on, "acquisition_cost": str(a.acquisition_cost),
        "useful_life_years": a.useful_life_years, "rate": str(a.rate) if a.rate is not None else None, "business_use_percent": str(a.business_use_percent),
        "months_in_service": e.months_in_service, "amount_total": str(e.amount_total), "amount_business": str(e.amount_business),
        "amount_household": str(e.amount_household), "book_value_begin": str(e.book_value_begin), "book_value_end": str(e.book_value_end),
        "requested_amount": str(e.requested_amount) if e.requested_amount is not None else None, "calc_basis": e.calc_basis,
        "journal_id": e.journal_id, "store": a.store.name if a.store else "",
    }


def asset_payload(a: FixedAsset) -> dict:
    entries = list(a.entries.select_related("fiscal_year").order_by("fiscal_year__year"))
    accumulated = sum((e.amount_total for e in entries), ZERO)
    return {
        "id": a.id, "name": a.name, "kind": a.kind, "kind_label": a.get_kind_display(), "category": a.category, "acquired_on": a.acquired_on,
        "acquisition_cost": str(a.acquisition_cost), "business_use_percent": str(a.business_use_percent), "depreciation_method": a.depreciation_method,
        "method_label": a.get_depreciation_method_display(), "useful_life_years": a.useful_life_years, "rate": str(a.rate) if a.rate is not None else None,
        "memorandum_value": str(a.memorandum_value), "status": a.status, "status_label": a.get_status_display(), "disposed_on": a.disposed_on,
        "disposal_amount": str(a.disposal_amount) if a.disposal_amount is not None else None, "paid_from": a.paid_from, "evidence": a.evidence_id,
        "store": a.store_id, "store_name": a.store.name if a.store else "", "note": a.note, "accumulated": str(accumulated),
        "book_value": str(D(a.acquisition_cost) - accumulated), "years": [{"year": e.fiscal_year.year, "amount_total": str(e.amount_total)} for e in entries],
        "created_at": a.created_at,
    }


@transaction.atomic
def record_acquisition(asset: FixedAsset, user) -> None:
    """取得時の支払いの仕訳（paid_from が none 以外のとき）。資産／現金・預金・事業主借・未払金。"""
    credit = {"bank": "bank", "cash": "cash", "owner_personal": "owner_in", "card": "accrued_card"}.get(asset.paid_from)
    if credit is None:
        return
    lines = [{"side": "debit", "key": asset.asset_account_key, "amount": str(D(asset.acquisition_cost)), "memo": asset.name[:100]},
             {"side": "credit", "key": credit, "amount": str(D(asset.acquisition_cost)), "memo": f"{asset.name} 取得"[:100]}]
    j = books.create_manual_journal(asset.taxpayer, user, issue_date=asset.acquired_on, description=f"固定資産の取得 {asset.name}"[:200], lines=lines,
                                    entry_kind="manual", store=asset.store, source_type="asset_acq", source_id=str(asset.id))
    asset.acquisition_journal = j
    asset.save(update_fields=["acquisition_journal", "updated_at"])


def method_options(filing_type: str = "white") -> list[dict]:
    blue = (filing_type or "").startswith("blue")
    return [
        {"key": "straight_line", "label": "定額法", "help": "取得価額 × 償却率 × 使用月数/12。個人の法定の方法。10万円以上の備品・設備はこれ", "available": True},
        {"key": "lump_sum_3yr", "label": "一括償却資産（20万円未満）", "help": "3年で均等に経費にする。月割なし", "available": True},
        {"key": "immediate", "label": "少額（10万円未満）", "help": "買った年に全額を消耗品費にする", "available": True},
        {"key": "blue_small_asset", "label": "30万円未満の特例（青色のみ）", "help": "買った年に全額を経費にできる（年300万円まで）。白色申告では使えない", "available": blue},
        {"key": "voluntary", "label": "任意償却（開業費）", "help": "開業費などの繰延資産。年度ごとに償却する額を決める", "available": True},
        {"key": "declining_balance", "label": "定率法", "help": "税務署への届出が必要。未対応", "available": False},
    ]


def now():
    return timezone.now()


def is_fiscal_year_locked(fy: FiscalYear) -> bool:
    return fy.is_locked
