"""
終了時間アラームの判定。保存された終了予定時刻と現在時刻から毎回再計算する（端末内カウントに依存しない）。

- 終了10分前: warn10（黄色）残り時間を表示
- 終了5分前 : warn5 （オレンジ）「確認してください」
- 終了時刻  : overdue（赤）「時間終了・対応待ち」
店舗設定 alarm_mode: "10" / "5" / "both"

戻り値の "message" が表示文言。"label" は後方互換のため同じ文言を入れる（卓一覧の alarms では対象名で上書きされる）。
"""
from __future__ import annotations

from datetime import datetime

WARN10_SECONDS = 10 * 60
WARN5_SECONDS = 5 * 60


def _state(level: str, message: str, remaining: int, entry) -> dict:
    return {"level": level, "message": message, "label": message, "remaining_seconds": remaining, "planned_end_at": entry.planned_end_at}


def alarm_state(entry, now: datetime, alarm_mode: str) -> dict | None:
    if not entry.is_active or entry.is_unlimited or entry.planned_end_at is None:
        return None
    remaining = int((entry.planned_end_at - now).total_seconds())
    if remaining <= 0:
        return _state("overdue", "時間終了・対応待ち", remaining, entry)
    if remaining <= WARN5_SECONDS:
        if alarm_mode in ("5", "both"):
            return _state("warn5", "確認してください", remaining, entry)
        # 10分前のみの設定: 10分前の警告を継続表示
        return _state("warn10", "終了間近", remaining, entry)
    if remaining <= WARN10_SECONDS and alarm_mode in ("10", "both"):
        return _state("warn10", "終了間近", remaining, entry)
    return None
