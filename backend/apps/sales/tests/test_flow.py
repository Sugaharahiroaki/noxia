"""入店→注文→延長→無制限変更→会計→手書き→取消・返金・未収 の API フローテスト。"""
from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest import mock
from zoneinfo import ZoneInfo

from django.utils import timezone
from rest_framework.test import APITestCase

from apps.core.business_date import business_date_for
from apps.core.testing import client_for, standard_fixture
from apps.inventory.models import StockMovement
from apps.sales import services
from apps.sales.alarms import alarm_state
from apps.sales.models import Checkout, CourseEntry, OrderLine, Payment, Receivable, Refund, Visit
from apps.staff.models import BackEntry, BackRule

TOKYO = ZoneInfo("Asia/Tokyo")


def key():
    return {"HTTP_IDEMPOTENCY_KEY": str(uuid.uuid4())}


class FlowBase(APITestCase):
    open_register = True

    def setUp(self):
        self.fx = standard_fixture(open_register=self.open_register)
        self.store = self.fx.store
        self.c = client_for(self.fx.users["manager"], self.store)
        self.cast1 = self.fx.employees["cast1"]
        self.cast2 = self.fx.employees["cast2"]
        self.p = self.fx.products
        self.table = self.fx.tables[0]
        BackRule.objects.create(store=self.store, name="cast drink 50%", target_type="category_kind", category_kind="cast_drink",
                                calc_type="percent", value=Decimal(50), effective_from=date(2020, 1, 1))
        BackRule.objects.create(store=self.store, name="nomination 500", target_type="nomination", calc_type="fixed", value=Decimal(500), effective_from=date(2020, 1, 1))
        StockMovement.objects.create(store=self.store, product=self.p["champagne"], kind="receive", quantity=Decimal(6), occurred_at=timezone.now())
        StockMovement.objects.create(store=self.store, product=self.p["tequila"], kind="receive", quantity=Decimal(2), occurred_at=timezone.now())

    # helpers ---------------------------------------------------------
    def open_visit(self, entries=None, **extra):
        entries = entries or [{"label": "A", "guest_count": 1, "course_id": self.fx.hourly.id}]
        r = self.c.post("/api/visits", {"table_id": self.table.id, "entries": entries, **extra}, format="json", **key())
        self.assertEqual(r.status_code, 201, r.content)
        return r.json()

    def add_line(self, vid, body, expect=201):
        r = self.c.post(f"/api/visits/{vid}/lines", body, format="json", **key())
        self.assertEqual(r.status_code, expect, r.content)
        return r.json()

    def quote(self, vid):
        return Decimal(self.c.get(f"/api/visits/{vid}").json()["quote"]["total"])

    def checkout(self, vid, payments, line_ids=None, expect=201):
        body = {"payments": payments}
        if line_ids:
            body["line_ids"] = line_ids
        r = self.c.post(f"/api/visits/{vid}/checkouts", body, format="json", **key())
        self.assertEqual(r.status_code, expect, r.content)
        return r.json()


class OpenVisitTests(FlowBase):
    def test_open_with_hourly_and_unlimited_entries(self):
        v = self.open_visit([
            {"label": "A", "guest_count": 1, "course_id": self.fx.hourly.id, "employee_id": self.cast1.id},
            {"label": "B", "guest_count": 2, "course_id": self.fx.unlimited.id},
        ])
        self.assertEqual(v["guest_count"], 3)
        self.assertEqual(v["slip_number"], 1)
        self.assertEqual(Decimal(v["quote"]["total"]), Decimal("9500"))  # 2500 + 3500×2 (per_person)
        a = next(e for e in v["course_entries"] if e["label"] == "A")
        b = next(e for e in v["course_entries"] if e["label"] == "B")
        self.assertFalse(a["is_unlimited"])
        self.assertTrue(b["is_unlimited"])
        self.assertIsNone(b["planned_end_at"])
        started = datetime.fromisoformat(a["started_at"])
        planned = datetime.fromisoformat(a["planned_end_at"])
        self.assertEqual(planned - started, timedelta(minutes=60))
        self.assertEqual(a["employee_name"], "cast1")
        set_lines = [l for l in v["lines"] if l["line_type"] == "course_set"]
        self.assertEqual(len(set_lines), 2)
        self.assertEqual(sorted(Decimal(l["amount"]) for l in set_lines), [Decimal("2500"), Decimal("7000")])

    def test_per_group_pricing(self):
        self.fx.settings.price_basis = "per_group"
        self.fx.settings.save()
        v = self.open_visit([{"label": "G", "guest_count": 3, "course_id": self.fx.hourly.id}])
        self.assertEqual(Decimal(v["quote"]["total"]), Decimal("2500"))

    def test_open_requires_table_and_course(self):
        r = self.c.post("/api/visits", {"entries": [{"course_id": self.fx.hourly.id}]}, format="json", **key())
        self.assertEqual(r.status_code, 400)
        r = self.c.post("/api/visits", {"table_id": self.table.id, "entries": []}, format="json", **key())
        self.assertEqual(r.status_code, 400)

    def test_add_course_entry_midway(self):
        v = self.open_visit()
        r = self.c.post(f"/api/visits/{v['id']}/course-entries", {"label": "合流", "guest_count": 1, "course_id": self.fx.unlimited.id}, format="json", **key())
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()["guest_count"], 2)
        self.assertEqual(Decimal(r.json()["quote"]["total"]), Decimal("6000"))

    def test_move_table_keeps_lines_and_entries(self):
        v = self.open_visit()
        self.add_line(v["id"], {"kind": "product", "product_id": self.p["beer"].id, "quantity": 2})
        r = self.c.post(f"/api/visits/{v['id']}/move-table", {"table_id": self.fx.tables[1].id}, format="json", **key())
        self.assertEqual(r.status_code, 200, r.content)
        d = r.json()
        self.assertEqual(d["table"], self.fx.tables[1].id)
        self.assertEqual(len(d["lines"]), 2)
        self.assertEqual(len(d["course_entries"]), 1)
        self.assertEqual(Decimal(d["quote"]["total"]), Decimal("4100"))
        entry = CourseEntry.objects.get(id=d["course_entries"][0]["id"])
        self.assertTrue(entry.events.filter(kind="move_table").exists())


class LineTests(FlowBase):
    def test_back_targets_percent_must_sum_to_100(self):
        v = self.open_visit()
        self.add_line(v["id"], {"kind": "product", "product_id": self.p["cast_drink"].id, "quantity": 1,
                                "back_targets": [{"employee_id": self.cast1.id, "share_value": 60}, {"employee_id": self.cast2.id, "share_value": 50}]}, expect=400)
        d = self.add_line(v["id"], {"kind": "product", "product_id": self.p["cast_drink"].id, "quantity": 1,
                                    "back_targets": [{"employee_id": self.cast1.id, "share_value": 50}, {"employee_id": self.cast2.id, "share_value": 50}]})
        ln = next(l for l in d["lines"] if l["line_type"] == "product")
        self.assertEqual(len(ln["back_targets"]), 2)
        self.assertEqual(BackEntry.objects.filter(order_line_id=ln["id"]).count(), 2)

    def test_fee_unset_rejected_not_zero(self):
        self.fx.settings.nomination_fee = None
        self.fx.settings.save()
        v = self.open_visit()
        r = self.c.post(f"/api/visits/{v['id']}/lines", {"kind": "nomination", "employee_id": self.cast1.id}, format="json", **key())
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["error"]["code"], "fee_unset")
        self.assertFalse(OrderLine.objects.filter(visit_id=v["id"], line_type="nomination").exists())
        self.assertEqual(self.quote(v["id"]), Decimal("2500"))

    def test_nomination_and_douhan_fees(self):
        v = self.open_visit()
        d = self.add_line(v["id"], {"kind": "nomination", "employee_id": self.cast1.id})
        d = self.add_line(v["id"], {"kind": "douhan", "employee_id": self.cast1.id, "quantity": 2})
        self.assertEqual(Decimal(d["quote"]["breakdown"]["nomination"]), Decimal("1000"))
        self.assertEqual(Decimal(d["quote"]["breakdown"]["douhan"]), Decimal("4000"))
        self.assertEqual(Decimal(d["quote"]["total"]), Decimal("7500"))

    def test_discount_requires_reason_and_records_operator(self):
        v = self.open_visit()
        self.add_line(v["id"], {"kind": "discount", "amount": 500}, expect=400)
        d = self.add_line(v["id"], {"kind": "discount", "amount": 500, "reason": "常連割引"})
        ln = next(l for l in d["lines"] if l["line_type"] == "discount")
        self.assertEqual(Decimal(ln["amount"]), Decimal("-500"))
        self.assertEqual(ln["discount_reason"], "常連割引")
        self.assertEqual(OrderLine.objects.get(id=ln["id"]).created_by, self.fx.users["manager"])
        self.assertEqual(Decimal(d["quote"]["total"]), Decimal("2000"))

    def test_cancel_line_requires_reason_and_reverses_stock_and_backs(self):
        v = self.open_visit()
        d = self.add_line(v["id"], {"kind": "product", "product_id": self.p["cast_drink"].id, "quantity": 2, "back_targets": [{"employee_id": self.cast1.id}]})
        ln = next(l for l in d["lines"] if l["line_type"] == "product")
        d = self.add_line(v["id"], {"kind": "product", "product_id": self.p["champagne"].id, "quantity": 1})
        ch = next(l for l in d["lines"] if l["product"] == self.p["champagne"].id)
        from apps.inventory.services import current_stock
        self.assertEqual(current_stock(self.p["champagne"]), Decimal("5"))
        r = self.c.post(f"/api/visits/{v['id']}/lines/{ch['id']}/cancel", {}, format="json", **key())
        self.assertEqual(r.status_code, 400)
        r = self.c.post(f"/api/visits/{v['id']}/lines/{ch['id']}/cancel", {"reason": "誤注文"}, format="json", **key())
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(current_stock(self.p["champagne"]), Decimal("6"))
        canceled = OrderLine.objects.get(id=ch["id"])
        self.assertEqual(canceled.status, "cancelled")
        self.assertEqual(canceled.cancel_reason, "誤注文")
        self.assertEqual(canceled.cancelled_by, self.fx.users["manager"])
        self.assertEqual(Decimal(r.json()["quote"]["total"]), Decimal("5500"))
        # バック: 注文時点では pending。取消で reversed になり、確定分の打消しは不要
        r = self.c.post(f"/api/visits/{v['id']}/lines/{ln['id']}/cancel", {"reason": "取消"}, format="json", **key())
        self.assertEqual(r.status_code, 200)
        self.assertEqual(set(BackEntry.objects.filter(order_line_id=ln["id"]).values_list("status", flat=True)), {"reversed"})
        # 取消済みの再取消は 409
        r = self.c.post(f"/api/visits/{v['id']}/lines/{ln['id']}/cancel", {"reason": "x"}, format="json", **key())
        self.assertEqual(r.status_code, 409)

    def test_price_change_does_not_alter_existing_lines(self):
        v = self.open_visit()
        d = self.add_line(v["id"], {"kind": "product", "product_id": self.p["beer"].id, "quantity": 2})
        self.p["beer"].price = Decimal("1200")
        self.p["beer"].save()
        self.fx.hourly.price = Decimal("9999")
        self.fx.hourly.save()
        ln = OrderLine.objects.get(visit_id=v["id"], line_type="product")
        self.assertEqual(ln.unit_price, Decimal("800"))
        self.assertEqual(ln.amount, Decimal("1600"))
        self.assertEqual(self.quote(v["id"]), Decimal("4100"))
        d2 = self.add_line(v["id"], {"kind": "product", "product_id": self.p["beer"].id, "quantity": 1})
        self.assertEqual(Decimal(d2["quote"]["total"]), Decimal("5300"))


class ExtensionAndUnlimitedTests(FlowBase):
    def _entry(self, v):
        return CourseEntry.objects.get(visit_id=v["id"])

    def test_extend_adds_from_original_planned_end_not_now(self):
        v = self.open_visit([{"label": "A", "guest_count": 2, "course_id": self.fx.hourly.id}])
        entry = self._entry(v)
        now = timezone.now()
        past_end = now - timedelta(minutes=30)
        CourseEntry.objects.filter(id=entry.id).update(planned_end_at=past_end)
        r = self.c.post(f"/api/visits/{v['id']}/course-entries/{entry.id}/extend", {}, format="json", **key())
        self.assertEqual(r.status_code, 200, r.content)
        entry.refresh_from_db()
        self.assertEqual(entry.planned_end_at, past_end + timedelta(minutes=60))
        self.assertLess(entry.planned_end_at, now + timedelta(minutes=45))
        self.assertEqual(entry.extension_count, 1)
        ext = OrderLine.objects.get(visit_id=v["id"], line_type="extension")
        self.assertEqual(ext.amount, Decimal("5000"))  # 2500 × 2名
        self.assertEqual(Decimal(r.json()["quote"]["total"]), Decimal("10000"))
        ev = entry.events.get(kind="extend")
        self.assertEqual(ev.performed_by, self.fx.users["manager"])
        self.assertEqual(ev.from_planned_end_at, past_end)
        self.assertEqual(ev.to_planned_end_at, past_end + timedelta(minutes=60))

    def test_hourly_plus_one_extension_bills_5000(self):
        v = self.open_visit()
        entry = self._entry(v)
        r = self.c.post(f"/api/visits/{v['id']}/course-entries/{entry.id}/extend", {}, format="json", **key())
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Decimal(r.json()["quote"]["total"]), Decimal("5000"))

    def test_unlimited_entry_cannot_extend(self):
        v = self.open_visit([{"label": "U", "guest_count": 1, "course_id": self.fx.unlimited.id}])
        entry = self._entry(v)
        r = self.c.post(f"/api/visits/{v['id']}/course-entries/{entry.id}/extend", {}, format="json", **key())
        self.assertEqual(r.status_code, 409)

    def test_change_unlimited_fixed_price(self):
        v = self.open_visit([{"label": "A", "guest_count": 2, "course_id": self.fx.hourly.id}])
        entry = self._entry(v)
        r = self.c.get(f"/api/visits/{v['id']}/course-entries/{entry.id}/unlimited-preview")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Decimal(r.json()["amount"]), Decimal("2000"))
        r = self.c.post(f"/api/visits/{v['id']}/course-entries/{entry.id}/change-unlimited", {}, format="json", **key())
        self.assertEqual(r.status_code, 200, r.content)
        entry.refresh_from_db()
        self.assertTrue(entry.is_unlimited)
        self.assertIsNone(entry.planned_end_at)
        ln = OrderLine.objects.get(visit_id=v["id"], line_type="course_change")
        self.assertEqual(ln.amount, Decimal("2000"))
        self.assertEqual(Decimal(r.json()["quote"]["total"]), Decimal("7000"))
        self.assertIsNone(alarm_state(entry, timezone.now() + timedelta(hours=5), "both"))
        self.assertTrue(entry.events.filter(kind="change_unlimited").exists())
        r = self.c.post(f"/api/visits/{v['id']}/course-entries/{entry.id}/change-unlimited", {}, format="json", **key())
        self.assertEqual(r.status_code, 409)

    def test_change_unlimited_difference_mode(self):
        self.fx.hourly.price = Decimal("2000")
        self.fx.hourly.save()
        self.fx.settings.unlimited_change_mode = "difference"
        self.fx.settings.save()
        v = self.open_visit()
        entry = self._entry(v)
        r = self.c.post(f"/api/visits/{v['id']}/course-entries/{entry.id}/change-unlimited", {}, format="json", **key())
        self.assertEqual(r.status_code, 200)
        self.assertEqual(OrderLine.objects.get(visit_id=v["id"], line_type="course_change").amount, Decimal("1500"))

    def test_change_unlimited_unset_is_rejected_not_zero(self):
        self.fx.settings.unlimited_change_mode = "unset"
        self.fx.settings.save()
        v = self.open_visit()
        entry = self._entry(v)
        r = self.c.post(f"/api/visits/{v['id']}/course-entries/{entry.id}/change-unlimited", {}, format="json", **key())
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["error"]["code"], "unlimited_change_unset")
        entry.refresh_from_db()
        self.assertFalse(entry.is_unlimited)
        self.assertFalse(OrderLine.objects.filter(visit_id=v["id"], line_type="course_change").exists())
        self.assertEqual(self.quote(v["id"]), Decimal("2500"))
        # 無制限コース未設定でも拒否
        self.fx.settings.unlimited_change_mode = "fixed"
        self.fx.settings.default_unlimited_course = None
        self.fx.settings.save()
        r = self.c.post(f"/api/visits/{v['id']}/course-entries/{entry.id}/change-unlimited", {}, format="json", **key())
        self.assertEqual(r.status_code, 409)

    def test_change_unlimited_after_extension_modes(self):
        def run(mode, price=None):
            self.fx.settings.unlimited_change_after_ext_mode = mode
            self.fx.settings.unlimited_change_after_ext_price = price
            self.fx.settings.save()
            v = self.open_visit()
            entry = self._entry(v)
            self.c.post(f"/api/visits/{v['id']}/course-entries/{entry.id}/extend", {}, format="json", **key())
            return self.c.post(f"/api/visits/{v['id']}/course-entries/{entry.id}/change-unlimited", {}, format="json", **key()), v

        r, _ = run("unset")
        self.assertEqual(r.status_code, 409)
        r, v = run("same_as_change")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(OrderLine.objects.get(visit_id=v["id"], line_type="course_change").amount, Decimal("1000"))
        r, v = run("fixed", Decimal("500"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(OrderLine.objects.get(visit_id=v["id"], line_type="course_change").amount, Decimal("500"))
        r, v = run("difference")
        self.assertEqual(r.status_code, 200)
        # 3500 − (2500 + 2500) < 0 → 0円（明示的な計算結果）
        self.assertEqual(OrderLine.objects.get(visit_id=v["id"], line_type="course_change").amount, Decimal("0"))

    def test_cancel_extension_line_restores_planned_end(self):
        v = self.open_visit()
        entry = self._entry(v)
        original = entry.planned_end_at
        self.c.post(f"/api/visits/{v['id']}/course-entries/{entry.id}/extend", {}, format="json", **key())
        ext = OrderLine.objects.get(visit_id=v["id"], line_type="extension")
        r = self.c.post(f"/api/visits/{v['id']}/lines/{ext.id}/cancel", {"reason": "誤操作"}, format="json", **key())
        self.assertEqual(r.status_code, 200)
        entry.refresh_from_db()
        self.assertEqual(entry.planned_end_at, original)
        self.assertEqual(entry.extension_count, 0)


class AlarmTests(FlowBase):
    def _entry(self, minutes):
        now = timezone.now()
        return SimpleNamespace(is_active=True, is_unlimited=False, planned_end_at=now + timedelta(minutes=minutes)), now

    def test_alarm_levels_by_mode(self):
        for minutes, expected in [(11, {"10": None, "5": None, "both": None}),
                                  (9, {"10": "warn10", "5": None, "both": "warn10"}),
                                  (4, {"10": "warn10", "5": "warn5", "both": "warn5"}),
                                  (-1, {"10": "overdue", "5": "overdue", "both": "overdue"})]:
            for mode, level in expected.items():
                e, now = self._entry(minutes)
                st = alarm_state(e, now, mode)
                self.assertEqual(st["level"] if st else None, level, f"{minutes}min mode={mode}")
        e, now = self._entry(-1)
        self.assertEqual(alarm_state(e, now, "both")["label"], "時間終了・対応待ち")
        e, now = self._entry(4)
        self.assertEqual(alarm_state(e, now, "both")["label"], "確認してください")

    def test_unlimited_and_inactive_excluded(self):
        now = timezone.now()
        self.assertIsNone(alarm_state(SimpleNamespace(is_active=True, is_unlimited=True, planned_end_at=None), now, "both"))
        self.assertIsNone(alarm_state(SimpleNamespace(is_active=False, is_unlimited=False, planned_end_at=now - timedelta(minutes=5)), now, "both"))

    def test_floor_state_recomputes_from_saved_planned_end(self):
        v = self.open_visit()
        entry = CourseEntry.objects.get(visit_id=v["id"])
        r = self.c.get("/api/floor/state")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["alarms"], [])
        CourseEntry.objects.filter(id=entry.id).update(planned_end_at=timezone.now() + timedelta(minutes=4))
        r = self.c.get("/api/floor/state").json()
        self.assertEqual(len(r["alarms"]), 1)
        a = r["alarms"][0]
        self.assertEqual(a["level"], "warn5")
        self.assertEqual(a["visit_id"], v["id"])
        self.assertEqual(a["table_name"], self.table.name)
        self.assertEqual(a["label"], "A")
        self.assertTrue(0 < a["remaining_seconds"] <= 240)
        self.assertIn("server_now", r)
        CourseEntry.objects.filter(id=entry.id).update(planned_end_at=timezone.now() - timedelta(minutes=1))
        r = self.c.get("/api/floor/state").json()
        self.assertEqual(r["alarms"][0]["level"], "overdue")
        # 再読み込みしても未対応の時間超過は卓一覧に残る
        table = next(t for t in r["tables"] if t["id"] == self.table.id)
        self.assertEqual(table["visits"][0]["course_entries"][0]["alarm"]["level"], "overdue")
        # 無制限へ変更すると対象外
        self.c.post(f"/api/visits/{v['id']}/course-entries/{entry.id}/change-unlimited", {}, format="json", **key())
        self.assertEqual(self.c.get("/api/floor/state").json()["alarms"], [])

    def test_alarm_mode_setting_respected(self):
        self.fx.settings.alarm_mode = "5"
        self.fx.settings.save()
        v = self.open_visit()
        entry = CourseEntry.objects.get(visit_id=v["id"])
        CourseEntry.objects.filter(id=entry.id).update(planned_end_at=timezone.now() + timedelta(minutes=8))
        self.assertEqual(self.c.get("/api/floor/state").json()["alarms"], [])
        CourseEntry.objects.filter(id=entry.id).update(planned_end_at=timezone.now() + timedelta(minutes=3))
        self.assertEqual(self.c.get("/api/floor/state").json()["alarms"][0]["level"], "warn5")


class BusinessDateTests(FlowBase):
    open_register = False  # 指定した時刻で開店してから試す

    def open_register_at(self, when):
        from apps.cash.services import get_or_open_business_day

        with mock.patch("django.utils.timezone.now", return_value=when):
            return get_or_open_business_day(self.store, self.fx.users["manager"], opening_float=Decimal("30000"))

    def test_business_date_for_switch_time(self):
        switch = time(6, 0)
        self.assertEqual(business_date_for(datetime(2026, 1, 2, 1, 0, tzinfo=TOKYO), switch, TOKYO), date(2026, 1, 1))
        self.assertEqual(business_date_for(datetime(2026, 1, 2, 5, 59, tzinfo=TOKYO), switch, TOKYO), date(2026, 1, 1))
        self.assertEqual(business_date_for(datetime(2026, 1, 2, 6, 0, tzinfo=TOKYO), switch, TOKYO), date(2026, 1, 2))
        self.assertEqual(business_date_for(datetime(2026, 1, 2, 23, 30, tzinfo=TOKYO), switch, TOKYO), date(2026, 1, 2))
        # UTC で渡しても東京時間で判定する
        self.assertEqual(business_date_for(datetime(2026, 1, 1, 16, 0, tzinfo=ZoneInfo("UTC")), switch, TOKYO), date(2026, 1, 1))

    def test_visit_at_1am_belongs_to_previous_business_date(self):
        fake_now = datetime(2026, 3, 10, 1, 0, tzinfo=TOKYO)
        self.open_register_at(fake_now)  # 深夜1時に開店 → 営業日は前日（切替 6:00）
        with mock.patch("django.utils.timezone.now", return_value=fake_now):
            visit = services.open_visit(self.store, self.fx.users["manager"], {"table_id": self.table.id, "entries": [{"guest_count": 1, "course_id": self.fx.hourly.id}]})
        self.assertEqual(visit.business_date, date(2026, 3, 9))
        self.assertEqual(visit.opened_at, fake_now)
        entry = visit.course_entries.get()
        self.assertEqual(entry.planned_end_at, fake_now + timedelta(minutes=60))

    def test_checkout_after_midnight_stays_on_open_business_day(self):
        evening = datetime(2026, 3, 9, 22, 0, tzinfo=TOKYO)
        self.open_register_at(evening)
        with mock.patch("django.utils.timezone.now", return_value=evening):
            visit = services.open_visit(self.store, self.fx.users["manager"], {"table_id": self.table.id, "entries": [{"guest_count": 1, "course_id": self.fx.hourly.id}]})
        late = datetime(2026, 3, 10, 2, 30, tzinfo=TOKYO)
        with mock.patch("django.utils.timezone.now", return_value=late):
            co = services.confirm_checkout(self.store, self.fx.users["manager"], visit.id, {"payments": [{"method": "cash", "amount": "2500", "tendered": "3000"}]})
        self.assertEqual(co.business_date, date(2026, 3, 9))
        self.assertEqual(co.confirmed_at, late)
        self.assertEqual(co.payments.get().business_date, date(2026, 3, 9))


class CheckoutTests(FlowBase):
    def test_cash_plus_card_with_change(self):
        v = self.open_visit()
        d = self.add_line(v["id"], {"kind": "product", "product_id": self.p["beer"].id, "quantity": 2})
        total = Decimal(d["quote"]["total"])
        self.assertEqual(total, Decimal("4100"))
        co = self.checkout(v["id"], [{"method": "cash", "amount": "3100", "tendered": "5000"}, {"method": "card", "amount": "1000", "card_reference": "T-001"}])
        self.assertEqual(co["kind"], "full")
        self.assertEqual(co["status"], "confirmed")
        self.assertEqual(Decimal(co["total"]), Decimal("4100"))
        self.assertEqual(Decimal(co["tax_total"]), Decimal("372"))
        self.assertEqual(Decimal(co["total_excl_tax"]), Decimal("3728"))
        cash = next(p for p in co["payments"] if p["method"] == "cash")
        self.assertEqual(Decimal(cash["change_amount"]), Decimal("1900"))
        self.assertEqual(Decimal(cash["tendered"]), Decimal("5000"))
        card = next(p for p in co["payments"] if p["method"] == "card")
        self.assertEqual(card["card_reference"], "T-001")
        visit = Visit.objects.get(id=v["id"])
        self.assertEqual(visit.status, "closed")
        self.assertIsNotNone(visit.closed_at)
        self.assertFalse(visit.course_entries.filter(is_active=True).exists())
        self.assertEqual(co["number"], 1)
        self.assertEqual(set(OrderLine.objects.filter(visit=visit).values_list("checkout_id", flat=True)), {co["id"]})

    def test_payment_mismatch_rejected(self):
        v = self.open_visit()
        r = self.c.post(f"/api/visits/{v['id']}/checkouts", {"payments": [{"method": "cash", "amount": "2000", "tendered": "2000"}]}, format="json", **key())
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["error"]["code"], "payment_mismatch")
        self.assertEqual(Checkout.objects.count(), 0)
        r = self.c.post(f"/api/visits/{v['id']}/checkouts", {"payments": [{"method": "cash", "amount": "2500", "tendered": "2000"}]}, format="json", **key())
        self.assertEqual(r.status_code, 400)
        r = self.c.post(f"/api/visits/{v['id']}/checkouts", {"payments": []}, format="json", **key())
        self.assertEqual(r.status_code, 400)
        self.assertEqual(Visit.objects.get(id=v["id"]).status, "open")

    def test_split_checkout_by_lines(self):
        v = self.open_visit()
        d = self.add_line(v["id"], {"kind": "product", "product_id": self.p["beer"].id, "quantity": 1})
        d = self.add_line(v["id"], {"kind": "product", "product_id": self.p["champagne"].id, "quantity": 1})
        beer = next(l for l in d["lines"] if l["product"] == self.p["beer"].id)
        set_line = next(l for l in d["lines"] if l["line_type"] == "course_set")
        r = self.c.post(f"/api/visits/{v['id']}/quote", {"line_ids": [beer["id"], set_line["id"]]}, format="json")
        self.assertEqual(Decimal(r.json()["total"]), Decimal("3300"))
        co1 = self.checkout(v["id"], [{"method": "cash", "amount": "3300", "tendered": "3300"}], line_ids=[beer["id"], set_line["id"]])
        self.assertEqual(co1["kind"], "partial")
        self.assertEqual(Decimal(co1["tax_total"]), Decimal("300"))
        self.assertEqual(Visit.objects.get(id=v["id"]).status, "open")
        hw = self.c.get(f"/api/checkouts/{co1['id']}/handwrite").json()
        self.assertIn("分割", hw["scope_note"])
        self.assertEqual(Decimal(hw["total"]), Decimal("3300"))
        self.assertEqual(Decimal(hw["tax_total"]), Decimal("300"))
        self.assertEqual(self.quote(v["id"]), Decimal("30000"))
        co2 = self.checkout(v["id"], [{"method": "card", "amount": "30000"}])
        self.assertEqual(co2["kind"], "partial")
        self.assertEqual(Decimal(co2["tax_total"]), Decimal("2727"))
        self.assertEqual(Visit.objects.get(id=v["id"]).status, "closed")
        hw2 = self.c.get(f"/api/checkouts/{co2['id']}/handwrite").json()
        self.assertEqual(Decimal(hw2["visit_total_all_checkouts"]), Decimal("33300"))
        self.assertEqual(len(hw2["other_checkouts"]), 1)
        # 既に会計済みの明細を再指定 → 409
        r = self.c.post(f"/api/visits/{v['id']}/checkouts", {"line_ids": [beer["id"]], "payments": [{"method": "cash", "amount": "800"}]}, format="json", **key())
        self.assertIn(r.status_code, (400, 409))

    def test_discount_only_split_rejected(self):
        v = self.open_visit()
        d = self.add_line(v["id"], {"kind": "discount", "amount": 500, "reason": "x"})
        disc = next(l for l in d["lines"] if l["line_type"] == "discount")
        r = self.c.post(f"/api/visits/{v['id']}/checkouts", {"line_ids": [disc["id"]], "payments": [{"method": "cash", "amount": "1"}]}, format="json", **key())
        self.assertEqual(r.status_code, 400)

    def test_handwrite_returns_stored_amounts(self):
        v = self.open_visit()
        self.add_line(v["id"], {"kind": "product", "product_id": self.p["food"].id, "quantity": 2})  # 8% 1000
        co = self.checkout(v["id"], [{"method": "cash", "amount": "1500", "tendered": "2000"}, {"method": "card", "amount": "2000"}])
        hw = self.c.get(f"/api/checkouts/{co['id']}/handwrite").json()
        self.assertEqual(Decimal(hw["total"]), Decimal("3500"))
        self.assertEqual(Decimal(hw["cash_paid"]), Decimal("1500"))
        self.assertEqual(Decimal(hw["card_paid"]), Decimal("2000"))
        self.assertEqual(Decimal(hw["cash_tendered"]), Decimal("2000"))
        self.assertEqual(Decimal(hw["cash_change"]), Decimal("500"))
        self.assertEqual(Decimal(hw["receivable_amount"]), Decimal("0"))
        self.assertEqual(Decimal(hw["tax_total"]), Decimal("301"))  # 227 (10%) + 74 (8%)
        rates = {row["rate"]: row["tax"] for row in hw["tax_breakdown"]}
        self.assertEqual(rates["10.00"], "227")
        self.assertEqual(rates["8.00"], "74")
        self.assertEqual(Decimal(hw["total_excl_tax"]), Decimal("3199"))
        # 会計後にコース料金を変更しても保存値は変わらない
        self.fx.hourly.price = Decimal("9000")
        self.fx.hourly.save()
        self.fx.settings.default_tax_rate = Decimal("20")
        self.fx.settings.save()
        hw2 = self.c.get(f"/api/checkouts/{co['id']}/handwrite").json()
        self.assertEqual(hw2["total"], hw["total"])
        self.assertEqual(hw2["tax_total"], hw["tax_total"])
        self.assertEqual(Checkout.objects.get(id=co["id"]).total, Decimal("3500"))

    def test_receivable_setting(self):
        self.fx.settings.receivable_allowed = False
        self.fx.settings.save()
        v = self.open_visit()
        r = self.c.post(f"/api/visits/{v['id']}/checkouts", {"payments": [{"method": "receivable", "amount": "2500", "debtor_name": "田中"}]}, format="json", **key())
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["error"]["code"], "receivable_not_allowed")
        self.fx.settings.receivable_allowed = True
        self.fx.settings.save()
        co = self.checkout(v["id"], [{"method": "cash", "amount": "500", "tendered": "500"}, {"method": "receivable", "amount": "2000", "debtor_name": "田中"}])
        rec = Receivable.objects.get(checkout_id=co["id"])
        self.assertEqual(rec.amount, Decimal("2000"))
        self.assertEqual(rec.debtor_name, "田中")
        self.assertEqual(rec.status, "open")
        hw = self.c.get(f"/api/checkouts/{co['id']}/handwrite").json()
        self.assertEqual(Decimal(hw["receivable_amount"]), Decimal("2000"))
        self.assertEqual(Decimal(hw["cash_paid"]), Decimal("500"))

    def test_receivable_collection_is_not_new_sale(self):
        v = self.open_visit()
        co = self.checkout(v["id"], [{"method": "receivable", "amount": "2500", "debtor_name": "田中"}])
        rec = Receivable.objects.get(checkout_id=co["id"])
        before = self.c.get("/api/sales/history").json()["summary"]
        self.assertEqual(Decimal(before["total"]), Decimal("2500"))
        self.assertEqual(Decimal(before["receivable"]), Decimal("2500"))
        day_before = self.c.get("/api/cash/current").json()["day"]["summary"]
        self.assertEqual(Decimal(day_before["cash_sales"]), Decimal("0"))
        r = self.c.post(f"/api/receivables/{rec.id}/collect", {"amount": "3000", "method": "cash"}, format="json", **key())
        self.assertEqual(r.status_code, 400)
        r = self.c.post(f"/api/receivables/{rec.id}/collect", {"amount": "1000", "method": "cash"}, format="json", **key())
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()["status"], "open")
        self.assertEqual(Decimal(r.json()["remaining"]), Decimal("1500"))
        r = self.c.post(f"/api/receivables/{rec.id}/collect", {"amount": "1500", "method": "card"}, format="json", **key())
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.json()["status"], "collected")
        after = self.c.get("/api/sales/history").json()["summary"]
        self.assertEqual(after["total"], before["total"])
        self.assertEqual(after["checkout_count"], before["checkout_count"])
        day_after = self.c.get("/api/cash/current").json()["day"]["summary"]
        self.assertEqual(Decimal(day_after["receivable_collected_cash"]), Decimal("1000"))
        self.assertEqual(Decimal(day_after["receivable_collected_card"]), Decimal("1500"))
        self.assertEqual(Decimal(day_after["cash_sales"]), Decimal("0"))
        self.assertEqual(Decimal(day_after["theoretical_cash"]) - Decimal(day_before["theoretical_cash"]), Decimal("1000"))
        r = self.c.post(f"/api/receivables/{rec.id}/collect", {"amount": "1", "method": "cash"}, format="json", **key())
        self.assertEqual(r.status_code, 409)
        listed = self.c.get("/api/receivables?status=collected").json()
        self.assertEqual([x["id"] for x in listed], [rec.id])


class VoidRefundTests(FlowBase):
    def _confirmed(self):
        v = self.open_visit()
        self.add_line(v["id"], {"kind": "product", "product_id": self.p["cast_drink"].id, "quantity": 2, "back_targets": [{"employee_id": self.cast1.id}]})
        co = self.checkout(v["id"], [{"method": "cash", "amount": "3500", "tendered": "4000"}, {"method": "card", "amount": "2000"}])
        return v, co

    def test_void_creates_refunds_reverses_backs_returns_lines(self):
        v, co = self._confirmed()
        self.assertEqual(BackEntry.objects.filter(checkout_id=co["id"], status="confirmed").count(), 1)
        self.assertEqual(BackEntry.objects.get(checkout_id=co["id"]).amount, Decimal("1500"))
        r = self.c.post(f"/api/checkouts/{co['id']}/void", {}, format="json", **key())
        self.assertEqual(r.status_code, 400)
        r = self.c.post(f"/api/checkouts/{co['id']}/void", {"reason": "打ち間違い", "terminal_refund_done": True}, format="json", **key())
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["status"], "voided")
        self.assertEqual(r.json()["void_reason"], "打ち間違い")
        refunds = Refund.objects.filter(checkout_id=co["id"]).order_by("method")
        self.assertEqual(refunds.count(), 2)
        by_method = {rf.method: rf for rf in refunds}
        self.assertEqual(by_method["cash"].amount, Decimal("3500"))
        self.assertEqual(by_method["card"].amount, Decimal("2000"))
        self.assertTrue(by_method["card"].terminal_refund_done)
        for rf in refunds:
            self.assertIsNotNone(rf.payment_id)
            self.assertEqual(rf.payment.checkout_id, co["id"])
            self.assertEqual(rf.payment.status, "refunded")
        self.assertEqual(Payment.objects.filter(checkout_id=co["id"], status="valid").count(), 0)
        # バック打消し: 確定 1500 と打消し -1500 で合計 0
        entries = BackEntry.objects.filter(employee=self.cast1)
        self.assertEqual(sum(e.amount for e in entries if e.status == "confirmed"), Decimal("0"))
        self.assertTrue(entries.filter(reversal_of__isnull=False, amount=Decimal("-1500")).exists())
        visit = Visit.objects.get(id=v["id"])
        self.assertEqual(visit.status, "open")
        self.assertIsNone(visit.closed_at)
        self.assertEqual(OrderLine.objects.filter(visit=visit, checkout__isnull=True, status="active").count(), 2)
        self.assertEqual(self.quote(v["id"]), Decimal("5500"))
        # 現金返金は理論現金に反映、売上履歴では取消扱い
        day = self.c.get("/api/cash/current").json()["day"]["summary"]
        self.assertEqual(Decimal(day["cash_refunds"]), Decimal("3500"))
        self.assertEqual(Decimal(day["cash_sales"]), Decimal("0"))
        hist = self.c.get("/api/sales/history").json()["summary"]
        self.assertEqual(hist["voided_count"], 1)
        self.assertEqual(Decimal(hist["total"]), Decimal("0"))
        r = self.c.post(f"/api/checkouts/{co['id']}/void", {"reason": "x"}, format="json", **key())
        self.assertEqual(r.status_code, 409)
        # 戻った明細を再会計できる
        co2 = self.checkout(v["id"], [{"method": "cash", "amount": "5500", "tendered": "5500"}])
        self.assertEqual(Decimal(co2["total"]), Decimal("5500"))
        self.assertEqual(BackEntry.objects.filter(checkout_id=co2["id"], status="confirmed").count(), 1)

    def test_partial_refund_limits_and_links(self):
        v, co = self._confirmed()
        r = self.c.post(f"/api/checkouts/{co['id']}/refunds", {"amount": "6000", "method": "cash", "reason": "x"}, format="json", **key())
        self.assertEqual(r.status_code, 400)
        r = self.c.post(f"/api/checkouts/{co['id']}/refunds", {"amount": "500", "method": "cash"}, format="json", **key())
        self.assertEqual(r.status_code, 400)
        r = self.c.post(f"/api/checkouts/{co['id']}/refunds", {"amount": "500", "method": "cash", "reason": "サービス"}, format="json", **key())
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(Decimal(r.json()["amount"]), Decimal("500"))
        self.assertEqual(r.json()["checkout"], co["id"])
        r = self.c.post(f"/api/checkouts/{co['id']}/refunds", {"amount": "5100", "method": "card", "reason": "x"}, format="json", **key())
        self.assertEqual(r.status_code, 400)  # 5000 − 500 = 4500 までしか返金できない
        r = self.c.post(f"/api/checkouts/{co['id']}/refunds", {"amount": "4500", "method": "card", "reason": "x", "terminal_refund_done": False}, format="json", **key())
        self.assertEqual(r.status_code, 201)
        self.assertFalse(r.json()["terminal_refund_done"])
        co_obj = Checkout.objects.get(id=co["id"])
        self.assertEqual(co_obj.status, "confirmed")
        self.assertEqual(co_obj.total, Decimal("5500"))
        self.assertEqual(co_obj.refunds.count(), 2)
        detail = self.c.get(f"/api/checkouts/{co['id']}").json()
        self.assertEqual(len(detail["refunds"]), 2)
        day = self.c.get("/api/cash/current").json()["day"]["summary"]
        self.assertEqual(Decimal(day["cash_refunds"]), Decimal("500"))
        self.assertEqual(Decimal(day["card_refunds"]), Decimal("4500"))

    def test_visit_cancel(self):
        v = self.open_visit()
        self.add_line(v["id"], {"kind": "product", "product_id": self.p["champagne"].id, "quantity": 1})
        from apps.inventory.services import current_stock
        self.assertEqual(current_stock(self.p["champagne"]), Decimal("5"))
        r = self.c.post(f"/api/visits/{v['id']}/cancel", {"reason": "ノーショー"}, format="json", **key())
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["status"], "cancelled")
        self.assertEqual(current_stock(self.p["champagne"]), Decimal("6"))
        self.assertFalse(CourseEntry.objects.filter(visit_id=v["id"], is_active=True).exists())
        v2 = self.open_visit()
        self.checkout(v2["id"], [{"method": "cash", "amount": "2500"}])
        r = self.c.post(f"/api/visits/{v2['id']}/cancel", {"reason": "x"}, format="json", **key())
        self.assertEqual(r.status_code, 409)
