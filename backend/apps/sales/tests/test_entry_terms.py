"""
入店時にセットの料金・時間・チャージを選ぶ（入店画面で横にスワイプして選ぶ）。
- price / duration_minutes を渡さなければセット構成のとおり。duration_minutes = 0 は時間無制限
- charge = true ならセット構成のチャージ料を 1 人 1 行の「チャージ」明細で入れる（チャージカットは 1 行ずつ取消）
"""
from __future__ import annotations

import uuid
from datetime import timedelta
from decimal import Decimal

from rest_framework.test import APITestCase

from apps.core.testing import client_for, standard_fixture
from apps.sales.models import CourseEntry, CourseEvent, OrderLine


def key():
    return {"HTTP_IDEMPOTENCY_KEY": str(uuid.uuid4())}


class EntryTermsTests(APITestCase):
    def setUp(self):
        self.fx = standard_fixture()
        self.c = client_for(self.fx.users["manager"], self.fx.store)
        self.course = self.fx.hourly  # 2,500 円・60 分
        self.course.charge_amount = Decimal(500)
        self.course.save()

    def open(self, entries, table=0, expect=201):
        r = self.c.post("/api/visits", {"table_id": self.fx.tables[table].id, "entries": entries}, format="json", **key())
        self.assertEqual(r.status_code, expect, r.content)
        return r.json()

    def quote(self, vid):
        return self.c.get(f"/api/visits/{vid}").json()["quote"]

    def test_defaults_follow_course(self):
        v = self.open([{"course_id": self.course.id, "guest_count": 2}])
        e = CourseEntry.objects.get(visit_id=v["id"])
        self.assertEqual((e.course_price, e.is_unlimited), (Decimal("2500"), False))
        self.assertEqual(e.planned_end_at - e.started_at, timedelta(minutes=60))
        self.assertFalse(OrderLine.objects.filter(visit_id=v["id"], line_type="charge").exists())
        self.assertEqual(Decimal(self.quote(v["id"])["total"]), Decimal("5000"))
        self.assertEqual(CourseEvent.objects.get(course_entry=e, kind="start").note, "")

    def test_price_and_time_chosen_at_entry(self):
        v = self.open([{"course_id": self.course.id, "guest_count": 2, "price": 2000, "duration_minutes": 90}])
        e = CourseEntry.objects.get(visit_id=v["id"])
        self.assertEqual(e.course_price, Decimal("2000"))
        self.assertEqual(e.planned_end_at - e.started_at, timedelta(minutes=90))
        line = OrderLine.objects.get(visit_id=v["id"], line_type="course_set")
        self.assertEqual((line.unit_price, line.amount), (Decimal("2000"), Decimal("4000")))
        self.assertEqual(Decimal(self.quote(v["id"])["total"]), Decimal("4000"))
        note = CourseEvent.objects.get(course_entry=e, kind="start").note
        self.assertIn("料金 2,500円 → 2,000円", note)
        self.assertIn("時間 60分 → 90分", note)

    def test_zero_minutes_is_unlimited(self):
        v = self.open([{"course_id": self.course.id, "guest_count": 1, "duration_minutes": 0}])
        e = CourseEntry.objects.get(visit_id=v["id"])
        self.assertTrue(e.is_unlimited)
        self.assertIsNone(e.planned_end_at)
        floor = self.c.get("/api/floor/state").json()
        self.assertEqual(floor["alarms"], [])

    def test_individual_entries_with_own_terms(self):
        v = self.open([
            {"course_id": self.course.id, "guest_count": 1, "label": "お客様1", "price": 2500, "duration_minutes": 60},
            {"course_id": self.course.id, "guest_count": 1, "label": "お客様2", "price": 3000, "duration_minutes": 0},
        ])
        es = list(CourseEntry.objects.filter(visit_id=v["id"]).order_by("id"))
        self.assertEqual([(e.course_price, e.is_unlimited) for e in es], [(Decimal("2500"), False), (Decimal("3000"), True)])
        self.assertEqual(Decimal(self.quote(v["id"])["total"]), Decimal("5500"))

    def test_charge_one_line_per_person_and_cut(self):
        v = self.open([{"course_id": self.course.id, "guest_count": 2, "charge": True}])
        charges = list(OrderLine.objects.filter(visit_id=v["id"], line_type="charge").order_by("id"))
        self.assertEqual([(c.name, c.amount, c.quantity) for c in charges], [("チャージ", Decimal("500"), Decimal("1"))] * 2)
        self.assertEqual(Decimal(self.quote(v["id"])["total"]), Decimal("6000"))
        # チャージカット: 1 人分だけ取り消す
        r = self.c.post(f"/api/visits/{v['id']}/lines/{charges[0].id}/cancel", {"reason": "チャージカット"}, format="json", **key())
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(Decimal(self.quote(v["id"])["total"]), Decimal("5500"))
        # セットの明細はチャージが残っていても取り消せる（チャージはセットに紐づけない）
        set_line = OrderLine.objects.get(visit_id=v["id"], line_type="course_set")
        r = self.c.post(f"/api/visits/{v['id']}/lines/{set_line.id}/cancel", {"reason": "誤入力"}, format="json", **key())
        self.assertEqual(r.status_code, 200, r.content)

    def test_charge_counts_as_set_for_service_charge_target(self):
        st = self.fx.store.settings
        st.service_charge_enabled = True
        st.service_charge_rate = Decimal("10")
        st.service_charge_target = "course_only"
        st.save()
        v = self.open([{"course_id": self.course.id, "guest_count": 2, "charge": True}])
        q = self.quote(v["id"])
        self.assertEqual(Decimal(q["service_charge"]), Decimal("600"))  # (5,000 + 1,000) × 10%

    def test_charge_without_course_charge_is_rejected(self):
        self.course.charge_amount = Decimal(0)
        self.course.save()
        r = self.open([{"course_id": self.course.id, "guest_count": 1, "charge": True}], expect=400)
        self.assertIn("チャージ料が設定されていません", str(r))

    def test_invalid_terms_are_rejected(self):
        for bad in ({"price": -1}, {"price": "2500.5"}, {"price": "abc"}, {"price": 20000000},
                    {"duration_minutes": -30}, {"duration_minutes": 24 * 60 + 1}, {"duration_minutes": "x"}):
            self.open([{"course_id": self.course.id, "guest_count": 1, **bad}], expect=400)
        self.assertFalse(OrderLine.objects.filter(store=self.fx.store).exists())

    def test_preview_matches_open(self):
        entries = [{"course_id": self.course.id, "guest_count": 2, "price": 3000, "duration_minutes": 0, "charge": True}]
        p = self.c.post("/api/visits/preview", {"table_id": self.fx.tables[0].id, "entries": entries}, format="json")
        self.assertEqual(p.status_code, 200, p.content)
        body = p.json()
        self.assertEqual([(l["line_type"], l["amount"]) for l in body["lines"]], [("course_set", "6000.00"), ("charge", "1000.00")])
        self.assertIsNone(body["lines"][0]["duration_minutes"])
        v = self.open(entries)
        self.assertEqual(Decimal(body["quote"]["total"]), Decimal(self.quote(v["id"])["total"]))

    def test_other_store_course_is_rejected(self):
        other = standard_fixture(code="s2", company_code="c2")
        other.hourly.charge_amount = Decimal(500)
        other.hourly.save()
        self.open([{"course_id": other.hourly.id, "guest_count": 1, "price": 0, "charge": True}], expect=400)
        p = self.c.post("/api/visits/preview", {"table_id": self.fx.tables[0].id, "entries": [{"course_id": other.hourly.id, "guest_count": 1}]},
                        format="json")
        self.assertEqual(p.status_code, 400, p.content)

    def test_course_charge_and_store_steps_settings(self):
        r = self.c.patch(f"/api/store/courses/{self.course.id}", {"charge_amount": "-1"}, format="json")
        self.assertEqual(r.status_code, 400, r.content)
        r = self.c.patch(f"/api/store/courses/{self.course.id}", {"charge_amount": "1000"}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(Decimal(r.json()["charge_amount"]), Decimal("1000"))
        s = self.c.get("/api/store/settings").json()
        self.assertEqual((s["entry_price_step"], s["entry_minutes_step"]), (500, 30))
        r = self.c.put("/api/store/settings", {"entry_price_step": 100, "entry_minutes_step": 15}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        for bad in ({"entry_price_step": 5}, {"entry_minutes_step": 0}, {"entry_minutes_step": 240}):
            r = self.c.put("/api/store/settings", bad, format="json")
            self.assertEqual(r.status_code, 400, (bad, r.content))
