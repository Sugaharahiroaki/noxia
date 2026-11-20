"""T1: ホールのヘッダー集計（未精算・精算済・売上合計）、卓の色・区分、開始時刻の訂正。"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from decimal import Decimal

from django.utils import timezone
from rest_framework.test import APITestCase

from apps.core.testing import client_for, make_store, standard_fixture
from apps.sales import services
from apps.sales.models import CourseEntry, CourseEvent, Visit


def key():
    return {"HTTP_IDEMPOTENCY_KEY": str(uuid.uuid4())}


class Base(APITestCase):
    def setUp(self):
        self.fx = standard_fixture()
        self.store = self.fx.store
        self.user = self.fx.users["manager"]
        self.c = client_for(self.user, self.store)

    def open_visit(self, table=0, guests=2, course=None):
        r = self.c.post("/api/visits", {"table_id": self.fx.tables[table].id, "entries": [{"guest_count": guests, "course_id": (course or self.fx.hourly).id}]}, format="json", **key())
        self.assertEqual(r.status_code, 201, r.content)
        return r.json()

    def floor(self):
        r = self.c.get("/api/floor/state")
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()

    def totals(self):
        f = self.floor()
        return Decimal(f["unpaid_total"]), Decimal(f["paid_total"]), Decimal(f["sales_total"])


class FloorTotalsTests(Base):
    def test_empty_floor_is_zero(self):
        self.assertEqual(self.totals(), (Decimal(0), Decimal(0), Decimal(0)))

    def test_totals_follow_open_checkout_and_cancel(self):
        v1 = self.open_visit(0, guests=2)  # 2,500 × 2
        self.assertEqual(self.totals(), (Decimal(5000), Decimal(0), Decimal(5000)))
        # 注文を足すと未精算が増える（伝票の running_total の合計と一致する）
        self.c.post(f"/api/visits/{v1['id']}/lines/batch", {"items": [{"kind": "product", "product_id": self.fx.products["beer"].id, "quantity": 2}]}, format="json", **key())
        v2 = self.open_visit(1, guests=1)
        f = self.floor()
        running = sum(Decimal(v["running_total"]) for t in f["tables"] for v in t["visits"])
        self.assertEqual(Decimal(f["unpaid_total"]), running)
        self.assertEqual(self.totals(), (Decimal(9100), Decimal(0), Decimal(9100)))
        # 会計すると未精算から精算済へ移る（売上合計は変わらない）
        co = services.confirm_checkout(self.store, self.user, v1["id"], {"payments": [{"method": "cash", "amount": "6600", "tendered": "10000"}]})
        self.assertEqual(self.totals(), (Decimal(2500), Decimal(6600), Decimal(9100)))
        # 営業中の伝票を取り消すと未精算から消える
        services.cancel_visit(self.store, self.user, v2["id"], "入力ミス")
        self.assertEqual(self.totals(), (Decimal(0), Decimal(6600), Decimal(6600)))
        # 会計の取消（返金記録つき）は精算済から外れ、伝票は営業中に戻る
        services.void_checkout(self.store, self.user, co.id, "打ち間違い")
        self.assertEqual(self.totals(), (Decimal(6600), Decimal(0), Decimal(6600)))

    def test_paid_total_matches_day_summary(self):
        from apps.cash.services import current_open_day, day_summary

        v = self.open_visit(0, guests=1)
        services.confirm_checkout(self.store, self.user, v["id"], {"payments": [{"method": "card", "amount": "2500"}]})
        self.assertEqual(Decimal(self.floor()["paid_total"]), Decimal(day_summary(current_open_day(self.store))["sales_total"]))

    def test_other_store_is_not_included(self):
        other = make_store("s2", company_code="c2")
        services.open_visit(other.store, self.user, {"table_id": other.tables[0].id, "entries": [{"guest_count": 3, "course_id": other.hourly.id}]})
        self.assertEqual(self.totals(), (Decimal(0), Decimal(0), Decimal(0)))

    def test_tables_carry_color_and_kind(self):
        t = self.fx.tables[0]
        owner = client_for(self.fx.users["owner"], self.store)
        r = owner.patch(f"/api/store/tables/{t.id}", {"color": "#3366cc", "kind": "BOX"}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        row = [x for x in self.floor()["tables"] if x["id"] == t.id][0]
        self.assertEqual((row["color"], row["kind"]), ("#3366cc", "BOX"))
        # 既存の卓は既定値（空）のまま表示できる
        other_row = [x for x in self.floor()["tables"] if x["id"] != t.id][0]
        self.assertEqual((other_row["color"], other_row["kind"]), ("", ""))
        self.assertEqual(owner.patch(f"/api/store/tables/{t.id}", {"color": "red"}, format="json").status_code, 400)

    def test_engaged_employees(self):
        cast1 = self.fx.employees["cast1"]
        cast2 = self.fx.employees["cast2"]
        v = self.open_visit(0)
        self.assertEqual(self.floor()["engaged_employee_ids"], [])
        services.add_fee_line(self.store, self.user, v["id"], "nomination", {"employee_id": cast1.id})
        # 商品のバック対象（ドリンクをもらっただけ）は「卓に付いている」とは数えない
        services.add_product_line(self.store, self.user, v["id"], {"product_id": self.fx.products["cast_drink"].id, "back_targets": [{"employee_id": cast2.id}]})
        self.assertEqual(self.floor()["engaged_employee_ids"], [cast1.id])


class ChangeEntryTimeTests(Base):
    def url(self, v, entry_id=None):
        return f"/api/visits/{v['id']}/course-entries/{entry_id or v['course_entries'][0]['id']}/change-time"

    def test_shifts_start_and_planned_end_and_records_event(self):
        v = self.open_visit()
        e = v["course_entries"][0]
        old_start = datetime.fromisoformat(e["started_at"])
        old_end = datetime.fromisoformat(e["planned_end_at"])
        new_start = (old_start - timedelta(minutes=20)).replace(second=0, microsecond=0)
        r = self.c.post(self.url(v), {"started_at": new_start.isoformat()}, format="json", **key())
        self.assertEqual(r.status_code, 200, r.content)
        e2 = r.json()["course_entries"][0]
        self.assertEqual(datetime.fromisoformat(e2["started_at"]), new_start)
        self.assertEqual(datetime.fromisoformat(e2["planned_end_at"]), old_end + (new_start - old_start))
        # 料金は変わらない
        self.assertEqual(Decimal(r.json()["quote"]["total"]), Decimal(v["quote"]["total"]))
        ev = CourseEvent.objects.filter(course_entry_id=e["id"], kind="change_time").get()
        self.assertEqual(ev.from_planned_end_at, old_end)
        self.assertIn("→", ev.note)
        # 最初のセットなら伝票の入店時刻もそろう（営業日は変わらない）
        visit = Visit.objects.get(id=v["id"])
        self.assertEqual(visit.opened_at, new_start)
        self.assertEqual(visit.business_date.isoformat(), v["business_date"])

    def test_extended_entry_keeps_extension(self):
        v = self.open_visit()
        e = v["course_entries"][0]
        r = self.c.post(f"/api/visits/{v['id']}/course-entries/{e['id']}/extend", {}, format="json", **key())
        extended_end = datetime.fromisoformat(r.json()["course_entries"][0]["planned_end_at"])
        new_start = (datetime.fromisoformat(e["started_at"]) - timedelta(minutes=10)).replace(second=0, microsecond=0)
        r = self.c.post(self.url(v), {"started_at": new_start.isoformat()}, format="json", **key())
        e2 = r.json()["course_entries"][0]
        self.assertEqual(datetime.fromisoformat(e2["planned_end_at"]), extended_end + (new_start - datetime.fromisoformat(e["started_at"])))
        self.assertEqual(e2["extension_count"], 1)

    def test_unlimited_entry_changes_start_only(self):
        v = self.open_visit(course=self.fx.unlimited)
        e = v["course_entries"][0]
        new_start = (datetime.fromisoformat(e["started_at"]) - timedelta(minutes=30)).replace(second=0, microsecond=0)
        r = self.c.post(self.url(v), {"started_at": new_start.isoformat()}, format="json", **key())
        self.assertEqual(r.status_code, 200, r.content)
        e2 = r.json()["course_entries"][0]
        self.assertEqual(datetime.fromisoformat(e2["started_at"]), new_start)
        self.assertIsNone(e2["planned_end_at"])

    def test_rejects_future_too_old_and_missing(self):
        v = self.open_visit()
        future = (timezone.now() + timedelta(minutes=10)).isoformat()
        old = (timezone.now() - timedelta(hours=30)).isoformat()
        for body in ({"started_at": future}, {"started_at": old}, {}, {"started_at": "abc"}):
            self.assertEqual(self.c.post(self.url(v), body, format="json", **key()).status_code, 400, body)
        self.assertFalse(CourseEvent.objects.filter(kind="change_time").exists())

    def test_rejects_checked_out_and_cancelled(self):
        v = self.open_visit()
        new_start = (timezone.now() - timedelta(minutes=30)).isoformat()
        # 分割会計でセットだけ会計済み（伝票は営業中のまま）→ そのセットは変更できない
        self.c.post(f"/api/visits/{v['id']}/lines/batch", {"items": [{"kind": "product", "product_id": self.fx.products["beer"].id}]}, format="json", **key())
        set_ids = [l.id for l in Visit.objects.get(id=v["id"]).lines.all() if l.line_type == "course_set"]  # 1 人 1 行
        services.confirm_checkout(self.store, self.user, v["id"], {"line_ids": set_ids, "payments": [{"method": "cash", "amount": "5000", "tendered": "5000"}]})
        r = self.c.post(self.url(v), {"started_at": new_start}, format="json", **key())
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["error"]["code"], "entry_closed")
        # 取消済みの伝票
        v2 = self.open_visit(1)
        services.cancel_visit(self.store, self.user, v2["id"], "入力ミス")
        self.assertEqual(self.c.post(self.url(v2), {"started_at": new_start}, format="json", **key()).status_code, 409)

    def test_other_store_ids_are_rejected(self):
        v = self.open_visit()
        new_start = (timezone.now() - timedelta(minutes=30)).isoformat()
        other = make_store("s2", company_code="c2")
        ov = services.open_visit(other.store, self.user, {"table_id": other.tables[0].id, "entries": [{"guest_count": 1, "course_id": other.hourly.id}]})
        oe = ov.course_entries.first()
        before = oe.started_at
        # 別店舗の伝票 ID
        self.assertEqual(self.c.post(f"/api/visits/{ov.id}/course-entries/{oe.id}/change-time", {"started_at": new_start}, format="json", **key()).status_code, 404)
        # 自店舗の伝票に別店舗のセット ID
        self.assertEqual(self.c.post(self.url(v, oe.id), {"started_at": new_start}, format="json", **key()).status_code, 404)
        self.assertEqual(CourseEntry.objects.get(id=oe.id).started_at, before)

    def test_replay_does_not_shift_twice(self):
        v = self.open_visit()
        e = v["course_entries"][0]
        new_start = (datetime.fromisoformat(e["started_at"]) - timedelta(minutes=15)).replace(second=0, microsecond=0)
        k = key()
        r1 = self.c.post(self.url(v), {"started_at": new_start.isoformat()}, format="json", **k)
        r2 = self.c.post(self.url(v), {"started_at": new_start.isoformat()}, format="json", **k)
        self.assertEqual((r1.status_code, r2.status_code), (200, 200))
        self.assertEqual(r2["Idempotent-Replayed"], "true")
        self.assertEqual(CourseEvent.objects.filter(course_entry_id=e["id"], kind="change_time").count(), 1)
        self.assertEqual(CourseEntry.objects.get(id=e["id"]).started_at, new_start)

    def test_cast_role_cannot_change_time(self):
        v = self.open_visit()
        cast = client_for(self.fx.users["cast"], self.store)
        r = cast.post(self.url(v), {"started_at": (timezone.now() - timedelta(minutes=30)).isoformat()}, format="json", **key())
        self.assertEqual(r.status_code, 403)
