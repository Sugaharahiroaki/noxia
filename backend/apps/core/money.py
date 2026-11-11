"""
金額計算ユーティリティ。金額は必ず Decimal で扱い、丸め方を明示する。
"""
from __future__ import annotations

from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal

ZERO = Decimal("0")
ONE = Decimal("1")
HUNDRED = Decimal("100")

_MODES = {
    "floor": ROUND_FLOOR,
    "ceil": ROUND_CEILING,
    "half_up": ROUND_HALF_UP,
}


def D(value) -> Decimal:
    """数値・文字列を Decimal に変換する（float は文字列化してから変換）。"""
    if isinstance(value, Decimal):
        return value
    if value is None:
        return ZERO
    if isinstance(value, float):
        return Decimal(str(value))
    return Decimal(value)


def round_yen(value: Decimal, mode: str = "floor", unit: int = 1) -> Decimal:
    """
    円単位（unit=1,10,100,...）に丸める。
    mode: floor / ceil / half_up
    負の値は絶対値で丸めて符号を戻す（値引きの切り捨てが「より大きな値引き」にならないようにする）。
    """
    value = D(value)
    if unit <= 0:
        unit = 1
    rounding = _MODES.get(mode, ROUND_FLOOR)
    sign = -1 if value < 0 else 1
    abs_value = abs(value)
    if unit == 1:
        result = abs_value.quantize(ONE, rounding=rounding)
    else:
        u = Decimal(unit)
        result = (abs_value / u).quantize(ONE, rounding=rounding) * u
    return (result * sign).quantize(ONE)


def tax_from_included(amount_incl: Decimal, rate_percent: Decimal, mode: str = "floor") -> Decimal:
    """税込金額に含まれる税額。 amount × rate / (100 + rate)"""
    amount_incl = D(amount_incl)
    rate = D(rate_percent)
    if rate == 0:
        return ZERO
    return round_yen(amount_incl * rate / (HUNDRED + rate), mode)


def tax_on_excluded(amount_excl: Decimal, rate_percent: Decimal, mode: str = "floor") -> Decimal:
    """税抜金額にかかる税額。 amount × rate / 100"""
    amount_excl = D(amount_excl)
    rate = D(rate_percent)
    if rate == 0:
        return ZERO
    return round_yen(amount_excl * rate / HUNDRED, mode)


def excl_from_included(amount_incl: Decimal, rate_percent: Decimal, mode: str = "floor") -> Decimal:
    """税込金額から税抜金額を求める（税額を丸めてから差し引く）。"""
    return D(amount_incl) - tax_from_included(amount_incl, rate_percent, mode)


def percent_of(amount: Decimal, rate_percent: Decimal, mode: str = "floor") -> Decimal:
    return round_yen(D(amount) * D(rate_percent) / HUNDRED, mode)


def fmt_yen(value) -> str:
    return f"{int(round_yen(D(value), 'half_up')):,}円"
