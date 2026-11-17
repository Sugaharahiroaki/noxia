"""
指名・同伴のバック（店舗共通）を、種類ごとの簡単な形で読み書きする（「指名・同伴」の設定画面）。

- 1 回ごと: 呼び・同伴・本指名・場内指名の明細ごとのバック。対象 yobi / douhan / nomination / jounai。固定額（円 × 回数）か料金の %。
- 会計の %: その種類のある会計に対するバック。対象 yobi_subtotal / douhan_subtotal / nomination_subtotal / jounai_subtotal。
  基準額（セット＋延長／会計全体 TAX 前／TAX 込み）と複数人のときの分け方は既存のルールの項目（backs.subtotal_back_base）。
- 中身は既存の BackRule の店舗共通ルール（employee なし）。キャスト個人別のルールには触らない（個人別が優先のまま）。
- 値を変えると、それまでのルールを前の営業日で終わらせ、いまの営業日から新しいルールにする（いまの営業日に始めたルールはそのまま書き換える）。
  付いたバック（BackEntry）は取引時点のルールを保存しているので、過去の分は変わらない。
"""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal, InvalidOperation

from apps.core.business_date import current_business_date
from apps.core.exceptions import ApiError
from apps.core.money import D

from .backs import models_q_effective_to
from .models import BackCalcType, BackRule, BackTargetType, SubtotalBackBasis, SubtotalBackSplit

# 並びは設定画面の並び（呼び・同伴・本指名・場内指名）
PER_UNIT = (BackTargetType.YOBI, BackTargetType.DOUHAN, BackTargetType.NOMINATION, BackTargetType.JOUNAI)
SUBTOTAL = (BackTargetType.YOBI_SUBTOTAL, BackTargetType.DOUHAN_SUBTOTAL, BackTargetType.NOMINATION_SUBTOTAL, BackTargetType.JOUNAI_SUBTOTAL)
TARGETS = tuple(str(t) for t in PER_UNIT + SUBTOTAL)
MODES = ("none", BackCalcType.FIXED.value, BackCalcType.PERCENT.value)
MAX_FIXED = Decimal("9999999")


def _current_common(store, target: str, on_date) -> list[BackRule]:
    """いまの営業日に効いている店舗共通ルール（効く順。先頭が使われる。backs.find_rule / find_subtotal_rule と同じ順）。"""
    qs = BackRule.objects.filter(store=store, employee__isnull=True, is_active=True, target_type=target, effective_from__lte=on_date).filter(
        models_q_effective_to(on_date))
    return sorted(qs, key=lambda r: (r.effective_from, r.id), reverse=True)


def _personal_count(store, target: str, on_date) -> int:
    return BackRule.objects.filter(store=store, employee__isnull=False, is_active=True, target_type=target, effective_from__lte=on_date).filter(
        models_q_effective_to(on_date)).count()


def _row(store, target: str, on_date) -> dict:
    rules = _current_common(store, target, on_date)
    r = rules[0] if rules else None
    return {
        "target": target,
        "kind": "subtotal" if target in SUBTOTAL else "per_unit",
        "mode": r.calc_type if r else "none",
        "value": str(r.value) if r else None,
        "subtotal_basis": r.subtotal_basis if r else SubtotalBackBasis.SUBTOTAL.value,
        "split_mode": r.split_mode if r else SubtotalBackSplit.EQUAL.value,
        "include_discount": r.include_discount if r else True,
        "rule_id": r.id if r else None,
        "rule_name": r.name if r else "",
        "effective_from": r.effective_from.isoformat() if r else None,
        "personal_rules": _personal_count(store, target, on_date),
    }


def payload(store) -> dict:
    on_date = current_business_date(store)
    return {"business_date": on_date.isoformat(), "backs": [_row(store, t, on_date) for t in TARGETS]}


def _clean(raw) -> list[dict]:
    if not isinstance(raw, list):
        raise ApiError("validation_error", "バックの設定を一覧で指定してください。")
    seen: set[str] = set()
    out = []
    for item in raw:
        if not isinstance(item, dict) or item.get("target") not in TARGETS:
            raise ApiError("validation_error", "バックの対象が不正です。")
        target = item["target"]
        if target in seen:
            raise ApiError("validation_error", "同じ対象が 2 回指定されています。")
        seen.add(target)
        mode = item.get("mode") or "none"
        if mode not in MODES:
            raise ApiError("validation_error", "バックの決め方は none / fixed / percent のどれかです。")
        row = {"target": target, "mode": mode}
        if mode != "none":
            try:
                value = D(str(item.get("value")).strip())
            except (InvalidOperation, ValueError, TypeError):
                raise ApiError("validation_error", "バックの金額・割合を数字で入れてください。")
            if not value.is_finite() or value < 0:
                raise ApiError("validation_error", "バックの金額・割合は 0 以上で入れてください。")
            if mode == "percent" and value > 100:
                raise ApiError("validation_error", "割合は 100% 以下で入れてください。")
            if mode == "percent" and value != value.quantize(Decimal("0.01")):
                raise ApiError("validation_error", "割合は小数第 2 位までで入れてください。")
            if mode == "fixed" and (value != value.to_integral_value() or value > MAX_FIXED):
                raise ApiError("validation_error", "固定額は円単位（整数）で入れてください。")
            row["value"] = value
        if target in SUBTOTAL:
            # 省略した項目は、いまのルールのまま（ルールが無ければ既定値）
            basis = item.get("subtotal_basis")
            split = item.get("split_mode")
            if basis is not None and basis not in SubtotalBackBasis.values:
                raise ApiError("validation_error", "バックの基準にする金額が不正です。")
            if split is not None and split not in SubtotalBackSplit.values:
                raise ApiError("validation_error", "複数人のときの分け方が不正です。")
            discount = item.get("include_discount")
            if discount is not None and not isinstance(discount, bool):
                raise ApiError("validation_error", "値引きを反映するかは true / false で指定してください。")
            row["subtotal_basis"] = basis
            row["split_mode"] = split
            row["include_discount"] = discount
        out.append(row)
    return out


def _rule_name(store, target: str) -> str:
    from apps.core import terms

    labels = terms.term_labels(store)
    names = {t: f"{labels[t]}バック" for t in ("yobi", "douhan", "nomination", "jounai")}
    names.update({f"{t}_subtotal": f"{labels[t]}のある会計の%バック" for t in ("yobi", "douhan", "nomination", "jounai")})
    return f"{names[target]}（店舗共通）"


def _same(rule: BackRule, fields: dict) -> bool:
    return all((D(getattr(rule, k)) if k == "value" else getattr(rule, k)) == v for k, v in fields.items())


def _end(rule: BackRule, on_date) -> None:
    """前の営業日で終わらせる。いまの営業日に始めたルールは止める（一度も使われない日付の範囲を残さない）。"""
    if rule.effective_from < on_date:
        rule.effective_to = on_date - timedelta(days=1)
        rule.save(update_fields=["effective_to", "updated_at"])
    else:
        rule.is_active = False
        rule.save(update_fields=["is_active", "updated_at"])


def _describe(rule: BackRule | None) -> str | None:
    if rule is None:
        return None
    text = f"{rule.value}%" if rule.calc_type == "percent" else f"{rule.value}円"
    if rule.target_type in SUBTOTAL:
        text += f"（{rule.get_subtotal_basis_display()}・複数人は{rule.get_split_mode_display()}・値引き{'反映' if rule.include_discount else '無視'}）"
    return text


def save(store, user, raw) -> dict:
    """
    指定した対象だけ変える（指定しない対象はそのまま）。変更点を {対象: {before, after}} で返す（操作履歴用）。
    呼び出し側でトランザクションを張ること。
    """
    rows = _clean(raw)
    on_date = current_business_date(store)
    changes: dict[str, dict] = {}
    for row in rows:
        target = row["target"]
        rules = _current_common(store, target, on_date)
        cur = rules[0] if rules else None
        before = _describe(cur)
        if row["mode"] == "none":
            for r in rules:
                _end(r, on_date)
            if cur is not None:
                changes[target] = {"before": before, "after": None}
            continue
        fields = {"calc_type": row["mode"], "value": row["value"]}
        if target in SUBTOTAL:
            for k, default in (("subtotal_basis", SubtotalBackBasis.SUBTOTAL.value), ("split_mode", SubtotalBackSplit.EQUAL.value), ("include_discount", True)):
                fields[k] = row[k] if row[k] is not None else (getattr(cur, k) if cur is not None else default)
        if cur is not None and _same(cur, fields):
            continue
        if cur is not None and cur.effective_from >= on_date:
            for k, v in fields.items():
                setattr(cur, k, v)
            cur.save()
            after = cur
        else:
            for r in rules:
                _end(r, on_date)
            base = {}
            if cur is not None:
                # それまでのルールの細かい条件（端数処理・確定のタイミング・税込/税抜の基準など）は引き継ぐ
                base = {k: getattr(cur, k) for k in ("basis", "include_discount", "include_service_charge", "rounding", "subtotal_basis", "split_mode", "confirm_timing")}
            after = BackRule.objects.create(
                store=store, employee=None, name=_rule_name(store, target), target_type=target, effective_from=on_date,
                note="「指名・同伴」の設定画面から登録", created_by=user, **{**base, **fields},
            )
        changes[target] = {"before": before, "after": _describe(after)}
    return changes
