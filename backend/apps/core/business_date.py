"""
営業日の計算。実日時と営業日を分ける。
店舗設定の「営業日切替時刻」（例: 06:00）より前の時刻は前日の営業分として扱う。
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.utils import timezone

TOKYO = ZoneInfo("Asia/Tokyo")


def store_tz(store) -> ZoneInfo:
    try:
        return ZoneInfo(getattr(store, "timezone", None) or "Asia/Tokyo")
    except Exception:
        return TOKYO


def business_date_for(dt: datetime, switch_time: time, tz: ZoneInfo = TOKYO) -> date:
    """dt（aware datetime）が属する営業日を返す。"""
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, tz)
    local = dt.astimezone(tz)
    if local.time() < switch_time:
        return (local - timedelta(days=1)).date()
    return local.date()


def business_date_now(store) -> date:
    settings = getattr(store, "settings", None)
    switch = settings.business_day_switch_time if settings else time(6, 0)
    return business_date_for(timezone.now(), switch, store_tz(store))


def current_business_date(store) -> date:
    """
    いまの営業日。レジ締めしていない営業日があれば、24時を過ぎてもその日（締めるまで同じ営業日）。
    営業日が開いていなければ、時刻と店舗の営業日切替時刻から決める。
    """
    from apps.cash.models import BusinessDay

    day = BusinessDay.objects.filter(store=store, status="open").order_by("-date").only("date").first()
    return day.date if day else business_date_now(store)


def business_day_range(business_date: date, switch_time: time, tz: ZoneInfo = TOKYO) -> tuple[datetime, datetime]:
    """営業日の開始・終了(aware datetime)。終了は排他的。"""
    start = datetime.combine(business_date, switch_time, tzinfo=tz)
    end = start + timedelta(days=1)
    return start, end
