"""従業員一覧の出勤状態（担当を選ぶときの「未出勤」警告に使う）。"""
from __future__ import annotations

import uuid
from datetime import timedelta

from django.utils import timezone
from rest_framework.test import APITestCase

from apps.core.business_date import business_date_now
from apps.core.testing import client_for, make_store, add_employee, standard_fixture
from apps.staff.models import Shift, ShiftBreak


def key():
    return {"HTTP_IDEMPOTENCY_KEY": str(uuid.uuid4())}


class ShiftStatusTests(APITestCase):
    def setUp(self):
        self.fx = standard_fixture()
        self.store = self.fx.store
        self.manager = client_for(self.fx.users["manager"], self.store)

    def statuses(self, client=None):
        rows = (client or self.manager).get("/api/staff/employees?active=1").json()
        return {r["display_name"]: (r["shift_status"], r["shift_since"]) for r in rows}

    def test_off_working_on_break_left(self):
        e = self.fx.employees
        bd = business_date_now(self.store)
        now = timezone.now()
        self.assertEqual(self.statuses()["cast1"][0], "off")
        # cast1: 出勤中
        self.assertEqual(self.manager.post("/api/staff/shifts/clock-in", {"employee_id": e["cast1"].id}, format="json", **key()).status_code, 201)
        # cast2: 休憩中
        s2 = Shift.objects.create(employee=e["cast2"], store=self.store, business_date=bd, clock_in=now - timedelta(hours=2))
        ShiftBreak.objects.create(shift=s2, started_at=now - timedelta(minutes=5))
        # staff1: 今日は退勤済み
        Shift.objects.create(employee=e["staff1"], store=self.store, business_date=bd, clock_in=now - timedelta(hours=5), clock_out=now - timedelta(hours=1))
        st = self.statuses()
        self.assertEqual(st["cast1"][0], "working")
        self.assertIsNotNone(st["cast1"][1])
        self.assertEqual(st["cast2"][0], "on_break")
        self.assertEqual(st["staff1"][0], "left")

    def test_other_store_shift_does_not_count(self):
        other = make_store("s2", company=self.fx.company)
        emp = self.fx.employees["cast1"]
        # 同じ会社の別店舗で出勤していても、この店舗では未出勤
        Shift.objects.create(employee=emp, store=other.store, business_date=business_date_now(other.store), clock_in=timezone.now())
        self.assertEqual(self.statuses()["cast1"][0], "off")
