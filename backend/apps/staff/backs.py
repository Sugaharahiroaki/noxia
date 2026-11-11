"""
バック計算。

- 「お客様に請求する料金（明細）」と「従業員へのバック（BackEntry）」を分ける。
- ルールの優先順位: 個人別 > 店舗共通。対象の具体性: 商品 > カテゴリ > カテゴリ種別 > 全商品。
- 計算条件はエントリにスナップショットし、後から設定を変えても確定済みの集計は変わらない。
- 複数従業員への分配: share_type=percent は合計100%を検証、fixed は固定額をそのまま付与。
- 取消・返金・担当変更は打消しエントリ（負の金額）で履歴を残す。
"""
from __future__ import annotations

from decimal import Decimal

from django.utils import timezone

from apps.core.business_date import business_date_now, current_business_date
from apps.core.exceptions import ApiError
from apps.core.money import D, ZERO, HUNDRED, percent_of, round_yen, tax_from_included

from .models import BackCalcType, BackEntry, BackEntryStatus, BackRule, BackTargetType


def validate_targets(targets: list[dict], line_amount: Decimal) -> list[dict]:
    """[{employee, share_type, share_value}] を検証して正規化する。"""
    if not targets:
        return []
    seen = set()
    cleaned = []
    percent_sum = ZERO
    for t in targets:
        emp = t["employee"]
        if emp.id in seen:
            raise ApiError("validation_error", "同じ従業員が複数回指定されています。")
        seen.add(emp.id)
        share_type = t.get("share_type") or "percent"
        if share_type not in ("percent", "fixed"):
            raise ApiError("validation_error", "分配方法は percent または fixed を指定してください。")
        value = D(t.get("share_value", 100 if share_type == "percent" else 0))
        if value < 0:
            raise ApiError("validation_error", "分配値は0以上で指定してください。")
        if share_type == "percent":
            percent_sum += value
        cleaned.append({"employee": emp, "share_type": share_type, "share_value": value})
    percent_targets = [t for t in cleaned if t["share_type"] == "percent"]
    if percent_targets and percent_sum != HUNDRED:
        raise ApiError("validation_error", f"割合分配の合計は100%にしてください（現在 {percent_sum}%）。")
    fixed_sum = sum((t["share_value"] for t in cleaned if t["share_type"] == "fixed"), ZERO)
    if fixed_sum > abs(D(line_amount)) and D(line_amount) != 0:
        raise ApiError("validation_error", "固定額バックの合計が明細金額を超えています。")
    return cleaned


def find_rule(store, employee, line, on_date) -> BackRule | None:
    from django.db.models import Q

    qs = BackRule.objects.filter(store=store, is_active=True, effective_from__lte=on_date).filter(
        models_q_effective_to(on_date)
    ).filter(Q(employee=employee) | Q(employee__isnull=True))
    candidates = [rule for rule in qs if _matches(rule, line)]
    if not candidates:
        return None
    candidates.sort(key=lambda r: (r.employee_id is not None, r.specificity(), r.effective_from, r.id), reverse=True)
    return candidates[0]


def models_q_effective_to(on_date):
    from django.db.models import Q
    return Q(effective_to__isnull=True) | Q(effective_to__gte=on_date)


def _matches(rule: BackRule, line) -> bool:
    lt = line.line_type
    if lt == "nomination":
        return rule.target_type == BackTargetType.NOMINATION
    if lt == "douhan":
        return rule.target_type == BackTargetType.DOUHAN
    if lt != "product":
        return False
    if rule.target_type == BackTargetType.ALL_PRODUCTS:
        return True
    if rule.target_type == BackTargetType.PRODUCT:
        return rule.product_id is not None and rule.product_id == line.product_id
    if rule.target_type == BackTargetType.CATEGORY:
        return rule.category_id is not None and rule.category_id == line.category_id_snapshot
    if rule.target_type == BackTargetType.CATEGORY_KIND:
        return bool(rule.category_kind) and rule.category_kind == line.category_kind
    return False


def line_basis(line, rule: BackRule, discount_ratio: Decimal = Decimal("1"), service_charge_rate: Decimal = ZERO) -> Decimal:
    amount = D(line.amount)
    if rule.basis == "tax_excluded" and line.tax_included:
        amount = amount - tax_from_included(amount, line.tax_rate, "floor")
    elif rule.basis == "tax_included" and not line.tax_included:
        amount = amount + round_yen(amount * D(line.tax_rate) / HUNDRED, "floor")
    if rule.include_discount and discount_ratio != 1:
        amount = round_yen(amount * discount_ratio, "floor")
    if rule.include_service_charge and service_charge_rate:
        amount = amount + percent_of(amount, service_charge_rate, "floor")
    return amount


def compute_amount(rule: BackRule, basis: Decimal, share: dict, quantity: Decimal) -> Decimal:
    if share["share_type"] == "fixed":
        return round_yen(share["share_value"], rule.rounding)
    if rule.calc_type == BackCalcType.FIXED:
        full = D(rule.value) * D(quantity)
    else:
        full = D(basis) * D(rule.value) / HUNDRED
    return round_yen(full * share["share_value"] / HUNDRED, rule.rounding)


def _live_entries(line):
    """有効なエントリ（打消し済み・打消しエントリ自体・取消状態を除く）。"""
    return BackEntry.objects.filter(
        order_line=line, status__in=[BackEntryStatus.PENDING, BackEntryStatus.CONFIRMED], reversal_of__isnull=True, reversals__isnull=True
    )


def create_entries_for_line(line, *, user, business_date, default_timing: str, now=None) -> list[BackEntry]:
    """注文時: 対象従業員ごとにルールを解決してエントリを作る（timing=order なら即確定）。"""
    now = now or timezone.now()
    entries = []
    store = line.store
    kind = line.line_type if line.line_type in ("nomination", "douhan") else "product"
    for target in line.back_targets.select_related("employee"):
        rule = find_rule(store, target.employee, line, business_date)
        if rule is None:
            continue
        share = {"share_type": target.share_type, "share_value": D(target.share_value)}
        basis = line_basis(line, rule)
        amount = compute_amount(rule, basis, share, line.quantity)
        timing = rule.confirm_timing or default_timing
        entry = BackEntry.objects.create(
            store=store, employee=target.employee, visit=line.visit, order_line=line, kind=kind, business_date=business_date,
            basis_amount=basis, share_percent=share["share_value"] if share["share_type"] == "percent" else ZERO,
            amount=amount, rule=rule, rule_snapshot=rule.snapshot(),
            status=BackEntryStatus.CONFIRMED if timing == "order" else BackEntryStatus.PENDING,
            confirm_timing=timing, confirmed_at=now if timing == "order" else None, created_by=user,
        )
        entries.append(entry)
    return entries


def settle_entries_for_checkout(checkout, lines, *, user, discount_ratio: Decimal, service_charge_rate: Decimal, has_receivable: bool, now=None):
    """
    会計確定時: 対象明細の未確定エントリを再計算して確定する。
    timing=payment で未収がある場合は、未収回収完了まで未確定のまま checkout に関連付ける。
    """
    now = now or timezone.now()
    for line in lines:
        if line.line_type not in ("product", "nomination", "douhan"):
            continue
        live = list(_live_entries(line).select_related("rule", "employee"))
        if not live and line.back_targets.exists():
            live = create_entries_for_line(line, user=user, business_date=checkout.business_date, default_timing="checkout", now=now)
        for entry in live:
            entry.checkout = checkout
            entry.business_date = checkout.business_date
            if entry.status == BackEntryStatus.PENDING:
                rule = entry.rule
                if rule is not None:
                    share = {"share_type": "percent" if entry.share_percent else "fixed",
                             "share_value": entry.share_percent if entry.share_percent else entry.amount}
                    if share["share_type"] == "percent":
                        entry.basis_amount = line_basis(line, rule, discount_ratio, service_charge_rate)
                        entry.amount = compute_amount(rule, entry.basis_amount, share, line.quantity)
                if entry.confirm_timing == "payment" and has_receivable:
                    pass  # 未収回収で確定
                else:
                    entry.status = BackEntryStatus.CONFIRMED
                    entry.confirmed_at = now
            entry.save()


def confirm_payment_timing_entries(checkout, *, now=None):
    now = now or timezone.now()
    BackEntry.objects.filter(checkout=checkout, status=BackEntryStatus.PENDING, confirm_timing="payment").update(
        status=BackEntryStatus.CONFIRMED, confirmed_at=now
    )


def reverse_entries(entries, *, user, reason: str, now=None):
    """確定済みは打消しエントリを作成、未確定は取消状態にする。"""
    now = now or timezone.now()
    created = []
    for entry in entries:
        if entry.status == BackEntryStatus.REVERSED or entry.reversals.exists():
            continue
        entry.reason = (entry.reason + " / " if entry.reason else "") + reason
        if entry.status == BackEntryStatus.CONFIRMED:
            # 確定済みは元のエントリを残し、打消し（負の金額）を打消し日の営業日に計上する → 合計は 0 になる
            created.append(BackEntry.objects.create(
                store=entry.store, employee=entry.employee, visit=entry.visit, order_line=entry.order_line, checkout=entry.checkout,
                kind=entry.kind, business_date=current_business_date(entry.store), basis_amount=entry.basis_amount, share_percent=entry.share_percent,
                amount=-entry.amount, rule=entry.rule, rule_snapshot=entry.rule_snapshot, status=BackEntryStatus.CONFIRMED,
                confirm_timing=entry.confirm_timing, confirmed_at=now, reversal_of=entry, reason=reason, created_by=user,
            ))
            entry.save(update_fields=["reason"])
        else:
            # 未確定は確定前なので取消状態にするだけ（集計対象外）
            entry.status = BackEntryStatus.REVERSED
            entry.save(update_fields=["status", "reason"])
    return created


def reverse_entries_for_line(line, *, user, reason: str):
    return reverse_entries(list(_live_entries(line)), user=user, reason=reason)


def reverse_entries_for_checkout(checkout, *, user, reason: str):
    qs = BackEntry.objects.filter(
        checkout=checkout, status__in=[BackEntryStatus.PENDING, BackEntryStatus.CONFIRMED], reversal_of__isnull=True, reversals__isnull=True
    )
    return reverse_entries(list(qs), user=user, reason=reason)
