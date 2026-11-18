"""
外に見せる ID（伝票 ID・会計 ID）。連番にしない（件数や順番を推測されないため）。

- 英数字（数字 2〜9 と英大文字。見間違えやすい 0・1・I・O は使わない）。
- 2026-11-17 から新しい ID は 8 文字（32^8 ≒ 1.1×10^12 通り。ぶつかったら作り直す）。それより前の ID は 12 文字のまま。
- 画面・印刷・CSV では 4 文字ごとに区切って表示する（例: K7QM-2XRA）。保存する値は区切りなし。
- 伝票番号・会計番号（店舗内の連番）は内部用。画面・印刷には出さず、この ID を出す（URL・既存データ・採番は変えない）。
"""
from __future__ import annotations

import secrets

ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
PUBLIC_ID_LENGTH = 8


def new_public_id() -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(PUBLIC_ID_LENGTH))


def ensure_unique_public_id(instance, field: str = "public_id", attempts: int = 20) -> None:
    """新しく保存する行の ID が、既存の行とぶつかっていたら作り直す（8 文字にしたので念のため）。"""
    model = type(instance)
    value = getattr(instance, field)
    for _ in range(attempts):
        if value and not model.objects.filter(**{field: value}).exists():
            break
        value = new_public_id()
    setattr(instance, field, value)


def format_public_id(value: str) -> str:
    value = value or ""
    return "-".join(value[i:i + 4] for i in range(0, len(value), 4))
