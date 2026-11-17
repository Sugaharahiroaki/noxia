"""
名称変更（店舗ごとの業務用語の上書き）。

- 対象は POS と集計画面の業務用語だけ。キーの一覧はここ 1 か所に定義する。
- 経理・税務の画面の用語、伝票の明細に保存済みの名前（取引時点の名前）、領収書は対象外。
- 上書きは表示の言い換えだけで、計算・明細の種別・バックの対象は変わらない。
"""
from __future__ import annotations

from .models import TermOverride

# キー → （初期値, 説明）。順番は設定画面の並び順
TERMS: dict[str, tuple[str, str]] = {
    "nomination": ("本指名", "来店前から決まっている指名"),
    "jounai": ("場内指名", "来店後に席で受けた指名"),
    "douhan": ("同伴", "キャストと一緒に来店"),
    "yobi": ("呼び", "キャストが呼んだお客様（指名の種類）と、本指名・場内指名・呼びのある伝票の売上の呼び方"),
    "set": ("セット", "時間制の料金（コース）"),
    "extension": ("延長", "セットの時間の延長"),
    "tag": ("タグ", "伝票に付ける目印"),
}
MAX_LABEL = 12


def term_labels(store) -> dict[str, str]:
    """キー → いまの表示名（上書きが無ければ初期値）。"""
    over = dict(TermOverride.objects.filter(store=store, key__in=TERMS).values_list("key", "label"))
    return {k: over.get(k) or default for k, (default, _d) in TERMS.items()}


def term_list(store) -> list[dict]:
    labels = term_labels(store)
    return [{"key": k, "default": default, "description": desc, "label": labels[k], "overridden": labels[k] != default}
            for k, (default, desc) in TERMS.items()]


def clean_labels(raw) -> dict[str, str]:
    """入力 {key: label} を検証する。空文字・初期値と同じ値は「初期値に戻す」。知らないキーはエラー。"""
    from .exceptions import ApiError

    if not isinstance(raw, dict):
        raise ApiError("validation_error", "名称の一覧を指定してください。")
    out: dict[str, str] = {}
    for key, label in raw.items():
        if key not in TERMS:
            raise ApiError("validation_error", f"変更できない名称です: {key}")
        label = str(label or "").strip()
        if len(label) > MAX_LABEL:
            raise ApiError("validation_error", f"名称は {MAX_LABEL} 文字以内で入力してください。")
        if any(c in label for c in "<>\"'&\n\r\t"):
            raise ApiError("validation_error", "名称に使えない文字が含まれています。")
        out[key] = "" if label == TERMS[key][0] else label
    # 同じ表示名が 2 つのキーに付くと画面で見分けがつかなくなる
    return out


def save_labels(store, labels: dict[str, str]) -> dict:
    """上書きを保存し、変わった内容 {key: {before, after}} を返す。"""
    from .exceptions import ApiError

    before = term_labels(store)
    after = dict(before)
    for key, label in labels.items():
        after[key] = label or TERMS[key][0]
    # 同じ表示名が 2 つのキーに付くと画面で見分けがつかなくなる（呼びは本指名とは別の種類なので、本指名を「呼び」にもできない）
    seen: dict[str, str] = {}
    for key, label in after.items():
        other = seen.get(label)
        if other is not None:
            raise ApiError("validation_error", f"「{label}」が {TERMS[other][0]} と {TERMS[key][0]} の両方に付いています。同じ名称を 2 つの用語に付けることはできません。")
        seen[label] = key
    for key, label in labels.items():
        if label:
            TermOverride.objects.update_or_create(store=store, key=key, defaults={"label": label})
        else:
            TermOverride.objects.filter(store=store, key=key).delete()
    return {k: {"before": before[k], "after": after[k]} for k in after if before[k] != after[k]}


def relabel(text: str, labels: dict[str, str]) -> str:
    """文字列の中の初期値の用語を、いまの表示名に置き換える（出力の見出し用）。長い用語から順に 1 回で置き換える。"""
    import re

    pairs = {TERMS[k][0]: v for k, v in labels.items() if v != TERMS[k][0]}
    if not pairs or not text:
        return text
    pattern = re.compile("|".join(re.escape(k) for k in sorted(pairs, key=len, reverse=True)))
    return pattern.sub(lambda m: pairs[m.group(0)], text)
