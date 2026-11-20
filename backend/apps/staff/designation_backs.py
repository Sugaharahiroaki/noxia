"""
指名・同伴のバック（店舗共通）を、種類ごとの簡単な形で読み書きする（「指名・同伴」の設定画面）。

- 1 回ごと: 呼び・同伴・本指名・場内指名の明細ごとのバック。対象 yobi / douhan / nomination / jounai。固定額（円 × 回数）か料金の %。
- 会計の %: その種類のある会計に対するバック。対象 yobi_subtotal / douhan_subtotal / nomination_subtotal / jounai_subtotal。
  基準額（セット＋延長／会計全体 TAX 前／TAX 込み）と複数人のときの分け方は既存のルールの項目（backs.subtotal_back_base）。
- 個人の%だけ（per_cast）: 店舗共通のバックはなし（画面では「なし」）にして、従業員管理で % を入れた人だけその % で付ける。
  基準額・分け方・値引きの扱いは店舗で 1 つ（StoreSettings.per_cast_backs）。% を入れていない人にはバックは付かない。
  基準額などを変えると、その種類のキャスト個人別のルールもそろえる（% はそのまま）。いまの画面で使うのは呼びだけ。
  店舗共通が「会計の%」のときも、% を入れた人はそちらが優先（backs.find_subtotal_rule の個人別優先）。
- 「使う／使わない」（StoreSettings.<種類>_enabled）は入店・卓の画面に出すかどうか。使わないにするとその種類の明細が付けられないので、
  個人の % も付かない（enabled で画面に知らせる）。
- 中身は既存の BackRule の店舗共通ルール（employee なし）。キャスト個人別のルールは、従業員ごとの読み書き（employee_payload / save_employee）
  と、個人別の基準額などをそろえるとき以外は触らない（個人別が優先のまま。backs.find_rule / find_subtotal_rule）。
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
KINDS = tuple(str(t) for t in PER_UNIT)
PER_CAST = "per_cast"
MODES = ("none", BackCalcType.FIXED.value, BackCalcType.PERCENT.value, PER_CAST)
MAX_FIXED = Decimal("9999999")
# 会計の % の計算条件（店舗で 1 つ。個人別のときは StoreSettings.per_cast_backs に持つ）
CONDITION_KEYS = ("subtotal_basis", "split_mode", "include_discount")
DEFAULT_CONDITIONS = {"subtotal_basis": SubtotalBackBasis.SUBTOTAL.value, "split_mode": SubtotalBackSplit.EQUAL.value, "include_discount": True}
# 新しいルールに前のルールから引き継ぐ細かい条件（端数処理・確定のタイミング・税込/税抜の基準など）
DETAIL_KEYS = ("basis", "include_discount", "include_service_charge", "rounding", "subtotal_basis", "split_mode", "confirm_timing")


def _kind(target: str) -> str:
    return str(target).removesuffix("_subtotal")


def _rules(store, target: str, on_date, employee=None) -> list[BackRule]:
    """いまの営業日に効いているルール（効く順。先頭が使われる。backs.find_rule / find_subtotal_rule と同じ順）。employee なし = 店舗共通。"""
    qs = BackRule.objects.filter(store=store, is_active=True, target_type=target, effective_from__lte=on_date).filter(models_q_effective_to(on_date))
    qs = qs.filter(employee__isnull=True) if employee is None else qs.filter(employee=employee)
    return sorted(qs, key=lambda r: (r.effective_from, r.id), reverse=True)


def _current_common(store, target: str, on_date) -> list[BackRule]:
    return _rules(store, target, on_date)


def _personal_count(store, target: str, on_date) -> int:
    return BackRule.objects.filter(store=store, employee__isnull=False, is_active=True, target_type=target, effective_from__lte=on_date).filter(
        models_q_effective_to(on_date)).count()


def _per_cast(store) -> dict[str, dict]:
    """{種類: 計算条件}。会計の % をキャストごとに決める種類だけ（壊れた値は読み飛ばす）。"""
    raw = store.settings.per_cast_backs or {}
    out: dict[str, dict] = {}
    if isinstance(raw, dict):
        for kind, cond in raw.items():
            if kind in KINDS and isinstance(cond, dict):
                out[kind] = {k: cond.get(k, DEFAULT_CONDITIONS[k]) for k in CONDITION_KEYS}
    return out


def _conditions(rule: BackRule | None, per_cast_cond: dict | None) -> dict:
    """会計の % の計算条件。店舗共通のルールがあればそれ、なければ個人別の条件、どちらも無ければ既定値。"""
    if rule is not None:
        return {k: getattr(rule, k) for k in CONDITION_KEYS}
    return dict(per_cast_cond or DEFAULT_CONDITIONS)


def _personal(store, target: str, on_date) -> list[dict]:
    """その種類にキャスト個人別のルールがある在籍中の人（1 人 1 行。効いているルール）。"""
    qs = BackRule.objects.filter(store=store, employee__isnull=False, employee__is_active=True, is_active=True, target_type=target,
                                 effective_from__lte=on_date).filter(models_q_effective_to(on_date)).select_related("employee")
    best: dict[int, BackRule] = {}
    for r in sorted(qs, key=lambda r: (r.effective_from, r.id), reverse=True):
        best.setdefault(r.employee_id, r)
    rows = [{"employee_id": r.employee_id, "name": r.employee.display_name, "calc_type": r.calc_type, "value": str(r.value)} for r in best.values()]
    return sorted(rows, key=lambda x: (x["name"], x["employee_id"]))


def _row(store, target: str, on_date, per_cast: dict) -> dict:
    rules = _current_common(store, target, on_date)
    r = rules[0] if rules else None
    subtotal = target in SUBTOTAL
    cast_cond = per_cast.get(_kind(target)) if subtotal and r is None else None
    row = {
        "target": target,
        "kind": "subtotal" if subtotal else "per_unit",
        "mode": r.calc_type if r else (PER_CAST if cast_cond else "none"),
        "value": str(r.value) if r else None,
        **_conditions(r, cast_cond),
        "rule_id": r.id if r else None,
        "rule_name": r.name if r else "",
        "effective_from": r.effective_from.isoformat() if r else None,
        "personal_rules": _personal_count(store, target, on_date),
        "enabled": getattr(store.settings, f"{_kind(target)}_enabled", True) is not False,
    }
    if subtotal:
        row["personal"] = _personal(store, target, on_date)
    return row


def payload(store) -> dict:
    on_date = current_business_date(store)
    per_cast = _per_cast(store)
    return {"business_date": on_date.isoformat(), "backs": [_row(store, t, on_date, per_cast) for t in TARGETS]}


def _percent(raw) -> Decimal:
    try:
        value = D(str(raw).strip())
    except (InvalidOperation, ValueError, TypeError):
        raise ApiError("validation_error", "バックの割合を数字で入れてください。")
    if not value.is_finite() or value < 0:
        raise ApiError("validation_error", "バックの割合は 0 以上で入れてください。")
    if value > 100:
        raise ApiError("validation_error", "割合は 100% 以下で入れてください。")
    if value != value.quantize(Decimal("0.01")):
        raise ApiError("validation_error", "割合は小数第 2 位までで入れてください。")
    return value


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
            raise ApiError("validation_error", "バックの決め方は none / fixed / percent / per_cast のどれかです。")
        if mode == PER_CAST and target not in SUBTOTAL:
            raise ApiError("validation_error", "個人の%だけにできるのは「会計の%」のバックだけです。")
        row = {"target": target, "mode": mode}
        if mode == "percent":
            row["value"] = _percent(item.get("value"))
        elif mode == "fixed":
            try:
                value = D(str(item.get("value")).strip())
            except (InvalidOperation, ValueError, TypeError):
                raise ApiError("validation_error", "バックの金額・割合を数字で入れてください。")
            if not value.is_finite() or value < 0:
                raise ApiError("validation_error", "バックの金額・割合は 0 以上で入れてください。")
            if value != value.to_integral_value() or value > MAX_FIXED:
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


def _rule_name(store, target: str, employee=None) -> str:
    from apps.core import terms

    labels = terms.term_labels(store)
    names = {t: f"{labels[t]}バック" for t in KINDS}
    names.update({f"{t}_subtotal": f"{labels[t]}のある会計の%バック" for t in KINDS})
    return f"{names[target]}（{employee.display_name if employee is not None else '店舗共通'}）"


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


def _apply(store, user, target: str, rules: list[BackRule], fields: dict, on_date, *, employee=None, note: str, template: BackRule | None = None) -> BackRule | None:
    """
    効いているルール（rules。先頭が使われている）を fields にする。変わらなければ None。
    いまの営業日に始めたルールはそのまま書き換え、それより前からのルールは前の営業日で終わらせて新しいルールを作る。
    新しいルールの細かい条件は、いまのルール（無ければ template）から引き継ぐ。
    """
    cur = rules[0] if rules else None
    if cur is not None and _same(cur, fields):
        return None
    if cur is not None and cur.effective_from >= on_date:
        for k, v in fields.items():
            setattr(cur, k, v)
        cur.save()
        return cur
    for r in rules:
        _end(r, on_date)
    src = cur or template
    base = {k: getattr(src, k) for k in ("calc_type", "value", *DETAIL_KEYS)} if src is not None else {}
    return BackRule.objects.create(
        store=store, employee=employee, name=_rule_name(store, target, employee), target_type=target, effective_from=on_date,
        note=note, created_by=user, **{**base, **fields},
    )


def _describe(rule: BackRule | None) -> str | None:
    if rule is None:
        return None
    text = f"{rule.value}%" if rule.calc_type == "percent" else f"{rule.value}円"
    if rule.target_type in SUBTOTAL:
        text += f"（{rule.get_subtotal_basis_display()}・複数人は{rule.get_split_mode_display()}・値引き{'反映' if rule.include_discount else '無視'}）"
    return text


def _describe_cast(cond: dict) -> str:
    return (f"店舗共通なし・個人の%だけ（{SubtotalBackBasis(cond['subtotal_basis']).label}・複数人は{SubtotalBackSplit(cond['split_mode']).label}"
            f"・値引き{'反映' if cond['include_discount'] else '無視'}）")


def _sync_personal(store, user, target: str, cond: dict, on_date) -> None:
    """個人別の種類の計算条件（基準額・分け方・値引き）を、その種類のキャスト個人別のルールにそろえる（% はそのまま）。"""
    ids = set(BackRule.objects.filter(store=store, employee__isnull=False, is_active=True, target_type=target, effective_from__lte=on_date).filter(
        models_q_effective_to(on_date)).values_list("employee_id", flat=True))
    from .models import Employee

    for emp in Employee.objects.filter(id__in=ids):
        _apply(store, user, target, _rules(store, target, on_date, emp), dict(cond), on_date, employee=emp,
               note="「指名・同伴」の設定画面（個人別の条件）から変更")


def save(store, user, raw) -> dict:
    """
    指定した対象だけ変える（指定しない対象はそのまま）。変更点を {対象: {before, after}} で返す（操作履歴用）。
    呼び出し側でトランザクションを張ること。
    """
    rows = _clean(raw)
    on_date = current_business_date(store)
    settings = store.settings
    per_cast = _per_cast(store)
    per_cast_before = dict(per_cast)
    changes: dict[str, dict] = {}
    for row in rows:
        target = row["target"]
        kind = _kind(target)
        rules = _current_common(store, target, on_date)
        cur = rules[0] if rules else None
        was_cast = target in SUBTOTAL and kind in per_cast
        before = _describe(cur) if cur is not None else (_describe_cast(per_cast[kind]) if was_cast else None)
        if was_cast and row["mode"] != PER_CAST:
            per_cast.pop(kind)
        if row["mode"] == "none":
            for r in rules:
                _end(r, on_date)
            if before is not None:
                changes[target] = {"before": before, "after": None}
            continue
        if row["mode"] == PER_CAST:
            for r in rules:
                _end(r, on_date)
            prev = _conditions(cur, per_cast.get(kind))
            cond = {k: row[k] if row[k] is not None else prev[k] for k in CONDITION_KEYS}
            per_cast[kind] = cond
            _sync_personal(store, user, target, cond, on_date)
            after = _describe_cast(cond)
            if after != before:
                changes[target] = {"before": before, "after": after}
            continue
        fields = {"calc_type": row["mode"], "value": row["value"]}
        if target in SUBTOTAL:
            prev = _conditions(cur, per_cast_before.get(kind) if cur is None else None)
            for k in CONDITION_KEYS:
                fields[k] = row[k] if row[k] is not None else prev[k]
        after = _apply(store, user, target, rules, fields, on_date, note="「指名・同伴」の設定画面から登録")
        if after is not None:
            changes[target] = {"before": before, "after": _describe(after)}
    if per_cast != per_cast_before:
        settings.per_cast_backs = per_cast
        settings.save(update_fields=["per_cast_backs", "updated_at"])
    return changes


# --- 従業員ごと（従業員管理の画面） --------------------------------------------------
def employee_payload(store, employee) -> dict:
    """その人の「会計の%」のバック（キャスト個人別のルール）と、店舗の設定（店舗共通／個人別／なし）。種類は会計の % の 4 つ。"""
    on_date = current_business_date(store)
    per_cast = _per_cast(store)
    rows = []
    for target in SUBTOTAL:
        t = str(target)
        kind = _kind(t)
        mine = _rules(store, t, on_date, employee)
        r = mine[0] if mine else None
        common = _current_common(store, t, on_date)
        c = common[0] if common else None
        cast_cond = per_cast.get(kind) if c is None else None
        rows.append({
            "target": t,
            "kind": kind,
            "calc_type": r.calc_type if r else None,
            "value": str(r.value) if r else None,
            "rule_id": r.id if r else None,
            "effective_from": r.effective_from.isoformat() if r else None,
            "store": {"mode": c.calc_type if c else (PER_CAST if cast_cond else "none"), "value": str(c.value) if c else None, **_conditions(c, cast_cond),
                      "enabled": getattr(store.settings, f"{kind}_enabled", True) is not False},
        })
    return {"business_date": on_date.isoformat(), "employee_id": employee.id, "backs": rows}


def save_employee(store, user, employee, raw) -> dict:
    """
    その人の「会計の%」のバックを決める。[{target: "yobi_subtotal", value: "20" | null}]（null・空 = 個人別をやめて店舗の設定に従う）。
    基準額・分け方・値引きの扱いは店舗の設定（店舗共通のルール、無ければ個人別の条件）にそろえる。変更点を返す（操作履歴用）。
    呼び出し側でトランザクションを張り、employee がこの店舗の従業員か確かめること。
    """
    if not isinstance(raw, list):
        raise ApiError("validation_error", "バックの設定を一覧で指定してください。")
    items: list[tuple[str, Decimal | None]] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict) or item.get("target") not in {str(t) for t in SUBTOTAL}:
            raise ApiError("validation_error", "バックの対象が不正です。")
        if item["target"] in seen:
            raise ApiError("validation_error", "同じ対象が 2 回指定されています。")
        seen.add(item["target"])
        value = item.get("value")
        items.append((item["target"], None if value is None or str(value).strip() == "" else _percent(value)))
    on_date = current_business_date(store)
    per_cast = _per_cast(store)
    changes: dict[str, dict] = {}
    for target, value in items:
        mine = _rules(store, target, on_date, employee)
        cur = mine[0] if mine else None
        before = _describe(cur)
        if value is None:
            for r in mine:
                _end(r, on_date)
            if cur is not None:
                changes[target] = {"before": before, "after": None}
            continue
        common = _current_common(store, target, on_date)
        c = common[0] if common else None
        cond = _conditions(c, per_cast.get(_kind(target)) if c is None else None)
        after = _apply(store, user, target, mine, {"calc_type": BackCalcType.PERCENT.value, "value": value, **cond}, on_date,
                       employee=employee, note="従業員管理から登録", template=c)
        if after is not None:
            changes[target] = {"before": before, "after": _describe(after)}
    return changes
