"""
キャスト報酬の項目分類・契約・税務区分・その他控除。

- 日払いの「お渡し額」（staff.payroll.daily_payout）を報酬項目（基本報酬・指名バック・ドリンクバック…）に分ける。
- 報酬項目ごとに「ホステス報酬の源泉徴収の対象か」を会社ごとに設定できる（PayItemSetting）。
  国税庁 No.2807 のとおり、報奨金・衣装代・深夜帰宅のタクシー代も対象に含むため、既定はすべて対象。
- 税務区分は契約（CastContract）で経営者が「確認済み」にしたものだけを使う。契約名称だけで決めない。
- 厚生費などの控除は源泉所得税とは別項目。インボイス未登録を理由とする一律の控除は既定で用意しない。
- 源泉所得税を差し引くかは店舗設定（StoreSettings.withholding_enabled）。オフなら税務区分も見ない。
- 店舗の控除項目（備品代など・報酬総額の%）は、お渡しのときにチェックしたものだけ差し引く。
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.db.models import Q

from apps.core.money import D, ZERO, percent_of

DEFAULT_PAY_ITEMS: dict[str, dict] = {
    "base_pay": {"label": "基本報酬（時給×勤務時間）", "withholding_target": True},
    "nomination_back": {"label": "指名バック", "withholding_target": True},
    "drink_back": {"label": "ドリンクバック", "withholding_target": True},
    "douhan_back": {"label": "同伴バック", "withholding_target": True},
    "bottle_back": {"label": "ボトル・シャンパンバック", "withholding_target": True},
    "extension_allowance": {"label": "延長に伴う報酬", "withholding_target": True},
    "other_back": {"label": "その他バック", "withholding_target": True},
    "back_adjustment": {"label": "バック調整", "withholding_target": True},
    "allowance": {"label": "その他手当（衣装代・送迎代等を含む）", "withholding_target": True},
}

# 商品のカテゴリ種別 → 報酬項目
_CATEGORY_ITEM = {"cast_drink": "drink_back", "drink": "drink_back", "shot": "drink_back", "bottle": "bottle_back", "champagne": "bottle_back"}

TAX_CHECKLIST = [
    ("command", "勤務時間・場所・接客方法について店の指揮命令を受けている"),
    ("time_bound", "出勤日・時間が店に拘束されている（遅刻・欠勤に罰金やペナルティがある）"),
    ("substitute", "本人の代わりに他の人が働くこと（再委託）が認められていない"),
    ("labor_based", "報酬が時間給など労務の提供時間に応じて決まる"),
    ("own_costs", "衣装・送迎などの費用を本人が負担していない"),
    ("exclusive", "他店で働くことが制限されている"),
]


def pay_items(company) -> dict[str, dict]:
    from .models import PayItemSetting

    items = {k: dict(v) for k, v in DEFAULT_PAY_ITEMS.items()}
    for s in PayItemSetting.objects.filter(company=company):
        items[s.code] = {"label": s.label, "withholding_target": s.withholding_target}
    return items


def contract_for(employee, on_date: date):
    from .models import CastContract

    return (
        CastContract.objects.filter(employee=employee, is_active=True, start_date__lte=on_date)
        .filter(Q(end_date__isnull=True) | Q(end_date__gte=on_date))
        .order_by("-start_date", "-id")
        .first()
    )


def taxpayer_for(store):
    profile = getattr(store, "tax_profile", None)
    try:
        return profile.taxpayer if profile else None
    except Exception:
        return None


def _store_settings(store):
    try:
        return store.settings
    except Exception:
        return None


def withholding_enabled(store) -> bool:
    s = _store_settings(store)
    return bool(s and s.withholding_enabled)


def store_deduction_items(store) -> list[dict]:
    s = _store_settings(store)
    return list(s.payout_deductions or []) if s else []


def tax_profile(employee, store, on_date: date) -> dict:
    """
    支払いに使う税務区分。確認済みの契約がなければ undetermined（要確認）。
    {category, label, confirmed, contract, period_type, reasons}
    """
    from .models import TaxCategory, TaxReviewStatus

    c = contract_for(employee, on_date)
    reasons = []
    if c is None:
        reasons.append("契約（契約区分・税務区分）が登録されていません。")
        return {"category": TaxCategory.UNDETERMINED, "label": "未確定（要確認）", "confirmed": False, "contract": None,
                "period_type": "unset", "reasons": reasons}
    category = c.tax_category
    confirmed = c.tax_review_status == TaxReviewStatus.CONFIRMED and category != TaxCategory.UNDETERMINED
    if not confirmed:
        reasons.append("税務区分が経営者の確認前です（契約名称だけでは給与か報酬かを決められません）。")
        category = TaxCategory.UNDETERMINED
    label = dict(TaxCategory.choices).get(category, category)
    return {"category": category, "label": label, "confirmed": confirmed, "contract": c, "period_type": c.calc_period, "reasons": reasons}


def classify_back(entry) -> str:
    if entry.kind == "nomination":
        return "nomination_back"
    if entry.kind == "douhan":
        return "douhan_back"
    if entry.kind == "adjustment":
        return "back_adjustment"
    if entry.kind in DEFAULT_PAY_ITEMS:
        return entry.kind
    kind = ""
    line = getattr(entry, "order_line", None)
    product = getattr(line, "product", None) if line is not None else None
    category = getattr(product, "category", None) if product is not None else None
    if category is not None:
        kind = category.kind
    elif entry.rule_snapshot:
        kind = entry.rule_snapshot.get("category_kind") or ""
    return _CATEGORY_ITEM.get(kind, "other_back")


def daily_items(store, employee, work_date: date, wage_amount: Decimal) -> list[dict]:
    """その日の報酬（時給分＋確定バック）を報酬項目ごとに分ける。"""
    from apps.staff.models import BackEntry, BackEntryStatus

    items_cfg = pay_items(store.company)
    totals: dict[str, Decimal] = {"base_pay": D(wage_amount)}
    entries = BackEntry.objects.filter(store=store, employee=employee, business_date=work_date, status=BackEntryStatus.CONFIRMED).select_related(
        "order_line__product__category"
    )
    for e in entries:
        code = classify_back(e)
        totals[code] = totals.get(code, ZERO) + D(e.amount)
    rows = []
    for code, amount in totals.items():
        if amount == 0 and code != "base_pay":
            continue
        cfg = items_cfg.get(code) or {"label": code, "withholding_target": True}
        rows.append({"code": code, "label": cfg["label"], "amount": str(amount), "withholding_target": bool(cfg["withholding_target"])})
    return rows


def target_of(items: list[dict]) -> Decimal:
    return sum((D(i["amount"]) for i in items if i.get("withholding_target")), ZERO)


def other_deductions(contract, day_gross: Decimal) -> list[dict]:
    """契約に設定した、源泉所得税以外の控除（1日分）。percent: 報酬総額の%、per_day: 1日あたり、per_payment: 1回あたり。"""
    if contract is None:
        return []
    rows = []
    for d in contract.deductions or []:
        try:
            value = D(d.get("value") or 0)
        except Exception:
            continue
        calc = d.get("calc")
        if calc == "percent":
            amount = percent_of(day_gross, value, "floor")
        elif calc in ("per_day", "per_payment"):
            amount = value
        else:
            continue
        if amount > 0:
            rows.append({"code": d.get("code") or "other", "label": d.get("label") or "その他控除", "calc": calc, "value": str(value), "amount": str(amount)})
    return rows


def deducted_by_code(payments: list[dict]) -> dict[str, Decimal]:
    """同じ勤務日の支払い（取消を除く）で、控除ごとに差し引き済みの額。"""
    out: dict[str, Decimal] = {}
    for p in payments:
        for d in p.get("deduction_items") or []:
            out[d["code"]] = out.get(d["code"], ZERO) + D(d["amount"])
    return out


def payout_deductions(store, contract, day_gross: Decimal, *, deducted: dict[str, Decimal], selected: list[str] | None) -> tuple[list[dict], list[dict]]:
    """
    今回の支払いで差し引く控除（applied）と、店舗の控除項目の選択肢（options）。
    契約の控除（厚生費など）は自動。店舗の控除項目はチェックしたもの（selected=None なら「最初からチェック」の項目）だけ。
    どちらもその日の報酬総額に対する額から、同じ日の支払いで差し引き済みの分を除く。
    """
    left = dict(deducted)

    def rest_of(code: str, amount: Decimal) -> Decimal:
        done = min(left.get(code, ZERO), amount)
        left[code] = left.get(code, ZERO) - done
        return amount - done

    applied = []
    for r in other_deductions(contract, day_gross):
        rest = rest_of(r["code"], D(r["amount"]))
        if rest > 0:
            applied.append({**r, "amount": str(rest)})
    options = []
    for it in store_deduction_items(store):
        day_amount = percent_of(day_gross, D(it["percent"]), "floor")
        if day_amount <= 0:
            continue
        rest = rest_of(it["code"], day_amount)
        checked = rest > 0 and (it["code"] in selected if selected is not None else bool(it.get("default_on")))
        options.append({"code": it["code"], "label": it["label"], "percent": str(it["percent"]), "default_on": bool(it.get("default_on")),
                        "amount": str(rest), "done": rest <= 0, "checked": checked})
        if checked:
            applied.append({"code": it["code"], "label": it["label"], "calc": "percent", "value": str(it["percent"]), "amount": str(rest), "optional": True})
    return applied, options
