"""年度別の計算モジュールの登録。年で式が変わる場合は rules/yYYYY.py を置き、ここで解決する。率・金額は必ず設定データ（TaxYearConfiguration）から読む。"""
from __future__ import annotations

from . import engine

_MODULES = {}


def engine_for(year: int):
    """年度の計算モジュール（既定は共通の engine）。"""
    return _MODULES.get(year, engine)
