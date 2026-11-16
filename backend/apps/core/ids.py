"""
外に見せる ID（伝票 ID・会計 ID）。連番にしない（件数や順番を推測されないため）。

- 12 文字の英数字（数字 2〜9 と英大文字。見間違えやすい 0・1・I・O は使わない）。32^12 ≒ 1.15×10^18 通り。
- 画面・CSV では 4 文字ごとに区切って表示する（例: K7QM-2XRA-9BHT）。保存する値は区切りなし。
- 内部の主キー（連番）や店舗内の伝票番号・会計番号はそのまま残す（URL・既存データ・採番を壊さない）。
"""
from __future__ import annotations

import secrets

ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
PUBLIC_ID_LENGTH = 12


def new_public_id() -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(PUBLIC_ID_LENGTH))


def format_public_id(value: str) -> str:
    value = value or ""
    return "-".join(value[i:i + 4] for i in range(0, len(value), 4))
