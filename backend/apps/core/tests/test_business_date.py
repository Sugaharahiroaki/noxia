"""営業日はレジ締めするまで続く（24時・切替時刻を過ぎても）。古い二重登録防止記録は締めで掃除される。"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from unittest import mock
from zoneinfo import ZoneInfo

from django.core.management import call_command
from django.utils import timezone
from rest_framework.test import APITestCase

from apps.core.models import IdempotencyRecord
from apps.core.testing import client_for, standard_fixture
from apps.sales import services
from apps.staff.models import Shift

TOKYO = ZoneInfo("Asia/Tokyo")


def key():
    return {"HTTP_IDEMPOTENCY_KEY": str(uuid.uuid4())}


class BusinessDateUntilClosingTests(APITestCase):
    def setUp(self):
        self.fx = standard_fixture(open_register=False)  # 指定した時刻で開店してから試す
        self.store = self.fx.store
        self.manager = client_for(self.fx.users["manager"], self.store)
        self.cast1 = self.fx.employees["cast1"]

    def at(self, y, m, d, hh, mm=0):
        return mock.patch("django.utils.timezone.now", return_value=datetime(y, m, d, hh, mm, tzinfo=TOKYO))

    def test_after_midnight_and_after_switch_time_still_same_business_day_until_closed(self):
        with self.at(2026, 10, 24, 19):
            self.assertEqual(self.manager.post("/api/cash/open", {"opening_float": "30000"}, format="json", **key()).status_code, 201)
            self.assertEqual(self.manager.get("/api/store/business-date").json()["business_date"], "2026-10-24")
        # 翌朝 7:00（切替時刻 6:00 を過ぎても）締めていなければ同じ営業日
        with self.at(2026, 10, 25, 7):
            self.assertEqual(self.manager.get("/api/store/business-date").json()["business_date"], "2026-10-24")
            r = self.manager.post("/api/staff/shifts/clock-in", {"employee_id": self.cast1.id}, format="json", **key())
            self.assertEqual(r.status_code, 201, r.content)
            self.assertEqual(Shift.objects.get(id=r.json()["id"]).business_date.isoformat(), "2026-10-24")
            # 勤怠一覧・日払いの初期表示もその営業日
            self.assertEqual(self.manager.get("/api/staff/shifts").json()["from"], "2026-10-24")
            self.assertEqual(self.manager.get("/api/staff/daily-payouts").json()["work_date"], "2026-10-24")
            self.manager.post(f"/api/staff/shifts/{r.json()['id']}/clock-out", {}, format="json", **key())
            r = self.manager.post("/api/cash/close", {"counted_cash": "30000", "next_day_float": "30000"}, format="json", **key())
            self.assertEqual(r.status_code, 200, r.content)
            # 締めたあとは時刻で決まる（7:00 は切替後なので 10/25）
            self.assertEqual(self.manager.get("/api/store/business-date").json()["business_date"], "2026-10-25")

    def test_before_switch_time_after_closing_is_previous_day(self):
        with self.at(2026, 10, 25, 2):
            self.assertEqual(self.manager.get("/api/store/business-date").json(), {"business_date": "2026-10-24", "day_open": False, "opened_at": None})

    def test_cast_can_read_business_date(self):
        cast = client_for(self.fx.users["cast"], self.store)
        self.assertEqual(cast.get("/api/store/business-date").status_code, 200)


class IdempotencyCleanupTests(APITestCase):
    def setUp(self):
        self.fx = standard_fixture()
        self.manager = client_for(self.fx.users["manager"], self.fx.store)

    def test_closing_purges_old_records_and_keeps_recent(self):
        self.manager.post("/api/cash/open", {"opening_float": "1000"}, format="json", **key())
        old = IdempotencyRecord.objects.create(user=self.fx.users["manager"], key="old", method="POST", path="/x", request_hash="h", state="done")
        IdempotencyRecord.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=3))
        r = self.manager.post("/api/cash/close", {"counted_cash": "1000", "next_day_float": "1000"}, format="json", **key())
        self.assertEqual(r.status_code, 200)
        self.assertFalse(IdempotencyRecord.objects.filter(key="old").exists())
        self.assertTrue(IdempotencyRecord.objects.exists())  # 直近の記録（今日の開店・締め）は残る

    def test_command(self):
        rec = IdempotencyRecord.objects.create(user=self.fx.users["manager"], key="k", method="POST", path="/x", request_hash="h")
        IdempotencyRecord.objects.filter(pk=rec.pk).update(created_at=timezone.now() - timedelta(days=10))
        call_command("cleanup_records", "--days", "7")
        self.assertFalse(IdempotencyRecord.objects.filter(pk=rec.pk).exists())
