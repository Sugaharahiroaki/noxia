"""
日本の祝日と、源泉所得税の納期限の計算。

- 祝日は「国民の祝日に関する法律」の現行ルール（2020年以降）で計算する。春分・秋分は 1980〜2099 年の近似式。
  政府が特別に祝日を追加・移動した年は settings.ACCOUNTING_EXTRA_HOLIDAYS（"YYYY-MM-DD" のリスト）で補う。
- 納期限が土曜日・日曜日・祝日・休日（12月29日〜1月3日を含む）に当たるときは、その翌日（休日明け）とする（国税通則法10条2項）。
"""
from __future__ import annotations

import calendar
from datetime import date, timedelta
from functools import lru_cache

from django.conf import settings


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """month 月の第 n weekday（月曜=0）。"""
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + 7 * (n - 1))


def _vernal_equinox(year: int) -> int:
    return int(20.8431 + 0.242194 * (year - 1980) - (year - 1980) // 4)


def _autumnal_equinox(year: int) -> int:
    return int(23.2488 + 0.242194 * (year - 1980) - (year - 1980) // 4)


@lru_cache(maxsize=64)
def holidays(year: int) -> dict[date, str]:
    days: dict[date, str] = {
        date(year, 1, 1): "元日",
        _nth_weekday(year, 1, 0, 2): "成人の日",
        date(year, 2, 11): "建国記念の日",
        date(year, 2, 23): "天皇誕生日",
        date(year, 3, _vernal_equinox(year)): "春分の日",
        date(year, 4, 29): "昭和の日",
        date(year, 5, 3): "憲法記念日",
        date(year, 5, 4): "みどりの日",
        date(year, 5, 5): "こどもの日",
        _nth_weekday(year, 7, 0, 3): "海の日",
        date(year, 8, 11): "山の日",
        _nth_weekday(year, 9, 0, 3): "敬老の日",
        date(year, 9, _autumnal_equinox(year)): "秋分の日",
        _nth_weekday(year, 10, 0, 2): "スポーツの日",
        date(year, 11, 3): "文化の日",
        date(year, 11, 23): "勤労感謝の日",
    }
    # 国民の休日: 前後が祝日の平日
    base = sorted(days)
    for a, b in zip(base, base[1:]):
        if (b - a).days == 2:
            mid = a + timedelta(days=1)
            if mid.weekday() != 6 and mid not in days:
                days[mid] = "国民の休日"
    # 振替休日: 祝日が日曜日なら、その後の最初の祝日でない日
    for d in sorted(days):
        if d.weekday() == 6:
            sub = d + timedelta(days=1)
            while sub in days:
                sub += timedelta(days=1)
            days[sub] = "振替休日"
    for s in getattr(settings, "ACCOUNTING_EXTRA_HOLIDAYS", []) or []:
        try:
            d = date.fromisoformat(s)
        except ValueError:
            continue
        if d.year == year:
            days[d] = "臨時の休日"
    return days


def is_holiday(d: date) -> bool:
    return d in holidays(d.year)


def is_tax_closed_day(d: date) -> bool:
    """国税の期限が延びる日: 土曜日・日曜日・祝日・休日、12月29日〜1月3日。"""
    if d.weekday() >= 5 or is_holiday(d):
        return True
    if (d.month == 12 and d.day >= 29) or (d.month == 1 and d.day <= 3):
        return True
    return False


def adjust_tax_deadline(d: date) -> date:
    while is_tax_closed_day(d):
        d += timedelta(days=1)
    return d


def month_end(d: date) -> date:
    return date(d.year, d.month, calendar.monthrange(d.year, d.month)[1])


def add_month(d: date, n: int = 1) -> date:
    y, m = divmod(d.month - 1 + n, 12)
    return date(d.year + y, m + 1, 1)


def monthly_due_date(paid_month: date) -> date:
    """支払った月の翌月10日（休日なら休日明け）。"""
    nxt = add_month(paid_month.replace(day=1))
    return adjust_tax_deadline(nxt.replace(day=10))


def special_due_period(paid_month: date) -> tuple[date, date, date]:
    """給与等の納期の特例: 1〜6月分は7月10日、7〜12月分は翌年1月20日。(期間開始, 期間終了, 納期限)"""
    y = paid_month.year
    if paid_month.month <= 6:
        return date(y, 1, 1), date(y, 6, 30), adjust_tax_deadline(date(y, 7, 10))
    return date(y, 7, 1), date(y, 12, 31), adjust_tax_deadline(date(y + 1, 1, 20))


def fiscal_year_code(pay_date: date) -> str:
    """納付書の「年度」欄: 納付する日の属する国の会計年度（4月〜翌3月）を令和の年2桁で。"""
    fy = pay_date.year if pay_date.month >= 4 else pay_date.year - 1
    return f"{fy - 2018:02d}"


def nouki_code(year: int, month: int) -> str:
    """「納期等の区分」欄: 元号（令和=5）＋年2桁＋月2桁。"""
    return f"5{year - 2018:02d}{month:02d}"


def reiwa_label(year: int, month: int | None = None) -> str:
    s = f"令和{year - 2018}年"
    return s + (f"{month}月" if month else "")
