"""T3: 伝票タグ、客の退店（在席人数）、延長の候補、途中合流の客の行。"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from decimal import Decimal

from rest_framework.test import APITestCase

from apps.catalog.models import Course, CourseExtensionOption
from apps.core.testing import client_for, make_store, standard_fixture
from apps.sales import services
from apps.sales.models import OrderLine, Tag, Visit, VisitGuest, VisitTag


def key():
    return {"HTTP_IDEMPOTENCY_KEY": str(uuid.uuid4())}


class Base(APITestCase):
    def setUp(self):
        # テスト用の料金: 60 分 3,500 円（1 人単位）、延長 30 分 2,000 円・60 分 3,500 円。本番の値ではない
        self.fx = standard_fixture()
        self.store = self.fx.store
        self.user = self.fx.users["manager"]
        self.c = client_for(self.user, self.store)
        self.owner = client_for(self.fx.users["owner"], self.store)
        self.cast1 = self.fx.employees["cast1"]
        self.set60 = Course.objects.create(store=self.store, name="セット60分", price=Decimal(3500), duration_minutes=60, extension_price=Decimal(3500), extension_minutes=60)
        self.opt30 = CourseExtensionOption.objects.create(course=self.set60, minutes=30, price=Decimal(2000), sort_order=1)
        self.opt60 = CourseExtensionOption.objects.create(course=self.set60, minutes=60, price=Decimal(3500), sort_order=2)

    def open(self, n=2, table=0, course=None, with_guests=True):
        body = {"table_id": self.fx.tables[table].id, "entries": [{"guest_count": n, "course_id": (course or self.set60).id}]}
        if with_guests:
            body["guests"] = [{} for _ in range(n)]
        r = self.c.post("/api/visits", body, format="json", **key())
        self.assertEqual(r.status_code, 201, r.content)
        return r.json()

    def get(self, vid):
        return self.c.get(f"/api/visits/{vid}").json()

    def floor_visit(self, vid):
        for t in self.c.get("/api/floor/state").json()["tables"]:
            for v in t["visits"]:
                if v["id"] == vid:
                    return v
        return None


class TagTests(Base):
    def setUp(self):
        super().setUp()
        self.yobi = Tag.objects.create(store=self.store, name="呼び", color="#d9534f", show_on_floor=True)
        self.hidden = Tag.objects.create(store=self.store, name="要確認", show_on_floor=False)

    def test_set_and_clear_tags_and_floor_state(self):
        v = self.open()
        r = self.c.put(f"/api/visits/{v['id']}/tags", {"tag_ids": [self.yobi.id, self.hidden.id]}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual([t["name"] for t in r.json()["tags"]], ["呼び", "要確認"])
        # ホールには「ホールに出す」タグだけ
        self.assertEqual([(t["name"], t["color"]) for t in self.floor_visit(v["id"])["tags"]], [("呼び", "#d9534f")])
        # 同じ内容をもう一度送っても重複しない
        self.c.put(f"/api/visits/{v['id']}/tags", {"tag_ids": [self.yobi.id, self.hidden.id]}, format="json")
        self.assertEqual(VisitTag.objects.filter(visit_id=v["id"]).count(), 2)
        r = self.c.put(f"/api/visits/{v['id']}/tags", {"tag_ids": []}, format="json")
        self.assertEqual(r.json()["tags"], [])
        self.assertEqual(self.floor_visit(v["id"])["tags"], [])
        # 金額は変わらない
        self.assertEqual(r.json()["quote"]["total"], v["quote"]["total"])

    def test_rejects_other_store_inactive_and_bad_input(self):
        v = self.open()
        other = make_store("s2", company_code="c2")
        other_tag = Tag.objects.create(store=other.store, name="呼び")
        stopped = Tag.objects.create(store=self.store, name="停止", is_active=False)
        for ids in ([other_tag.id], [stopped.id], [999999], "x", ["a"]):
            self.assertEqual(self.c.put(f"/api/visits/{v['id']}/tags", {"tag_ids": ids}, format="json").status_code, 400, ids)
        self.assertFalse(VisitTag.objects.exists())
        # 別店舗の伝票
        ov = services.open_visit(other.store, self.user, {"table_id": other.tables[0].id, "entries": [{"guest_count": 1, "course_id": other.hourly.id}]})
        self.assertEqual(self.c.put(f"/api/visits/{ov.id}/tags", {"tag_ids": [self.yobi.id]}, format="json").status_code, 404)

    def test_closed_visit_cannot_change_tags(self):
        v = self.open()
        services.cancel_visit(self.store, self.user, v["id"], "入力ミス")
        self.assertEqual(self.c.put(f"/api/visits/{v['id']}/tags", {"tag_ids": [self.yobi.id]}, format="json").status_code, 409)

    def test_master_api_permissions_and_scope(self):
        # 読むのは settings.view（スタッフ可）、書くのは catalog.edit（スタッフ不可・店長可）
        staff = client_for(self.fx.users["staff"], self.store)
        self.assertEqual(staff.get("/api/store/tags?active=1").status_code, 200)
        self.assertEqual(staff.post("/api/store/tags", {"name": "新規"}, format="json").status_code, 403)
        r = self.c.post("/api/store/tags", {"name": "新規", "color": "#1f6fd0", "show_on_floor": True}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(self.c.post("/api/store/tags", {"name": "新規"}, format="json").status_code, 400)  # 同じ名前
        self.assertEqual(self.c.post("/api/store/tags", {"name": "色", "color": "red"}, format="json").status_code, 400)
        # 別店舗のタグは見えず、変更もできない
        other = standard_fixture("s2", company_code="c2")
        ot = Tag.objects.create(store=other.store, name="他店")
        self.assertNotIn("他店", [t["name"] for t in self.c.get("/api/store/tags").json()])
        self.assertEqual(self.c.patch(f"/api/store/tags/{ot.id}", {"name": "x"}, format="json").status_code, 404)
        # 削除は停止（付け済みの伝票の表示は残る）
        v = self.open()
        self.c.put(f"/api/visits/{v['id']}/tags", {"tag_ids": [self.yobi.id]}, format="json")
        self.assertEqual(self.c.delete(f"/api/store/tags/{self.yobi.id}").status_code, 204)
        self.assertFalse(Tag.objects.get(id=self.yobi.id).is_active)
        self.assertEqual([t["name"] for t in self.get(v["id"])["tags"]], ["呼び"])


class LeaveGuestTests(Base):
    def leave(self, v, guest, **kw):
        return self.c.post(f"/api/visits/{v['id']}/guests/{guest['id']}/leave", {}, format="json", **(kw or key()))

    def test_leave_reduces_present_count_but_not_charges(self):
        v = self.open(3)
        self.assertEqual((v["guest_count"], v["present_count"]), (3, 3))
        r = self.leave(v, v["guests"][1])
        self.assertEqual(r.status_code, 200, r.content)
        d = r.json()
        self.assertEqual([g["status"] for g in d["guests"]], ["present", "left", "present"])  # 他の客は在席のまま
        self.assertIsNotNone(d["guests"][1]["left_at"])
        self.assertEqual((d["guest_count"], d["present_count"]), (3, 2))  # 来店人数は 3 のまま、在席は 2
        self.assertEqual(d["course_entries"][0]["present_count"], 2)
        # 開始済みのセットは減らさない（Q3 の扱い）
        self.assertEqual(Decimal(d["quote"]["total"]), Decimal(10500))
        self.assertEqual(self.floor_visit(v["id"])["present_count"], 2)

    def test_extension_after_leave_uses_present_count(self):
        v = self.open(3)
        e = v["course_entries"][0]
        self.leave(v, v["guests"][0])
        opts = self.c.get(f"/api/visits/{v['id']}/course-entries/{e['id']}/extend-options").json()["options"]
        self.assertEqual([(o["minutes"], Decimal(o["amount"]), o["guest_count"]) for o in opts], [(30, Decimal(4000), 2), (60, Decimal(7000), 2)])
        r = self.c.post(f"/api/visits/{v['id']}/course-entries/{e['id']}/extend", {"option_id": self.opt30.id}, format="json", **key())
        line = [l for l in r.json()["lines"] if l["line_type"] == "extension"][0]
        self.assertEqual((Decimal(line["amount"]), Decimal(line["quantity"]), line["extension_minutes"]), (Decimal(4000), Decimal(2), 30))
        self.assertEqual(Decimal(r.json()["quote"]["total"]), Decimal(10500 + 4000))

    def test_last_guest_cannot_leave(self):
        v = self.open(2)
        self.assertEqual(self.leave(v, v["guests"][0]).status_code, 200)
        r = self.leave(v, v["guests"][1])
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["error"]["code"], "last_guest")
        self.assertEqual(VisitGuest.objects.get(id=v["guests"][1]["id"]).status, "present")

    def test_already_left_and_closed_visit(self):
        v = self.open(3)
        self.leave(v, v["guests"][0])
        r = self.leave(v, v["guests"][0])
        self.assertEqual((r.status_code, r.json()["error"]["code"]), (409, "already_left"))
        services.cancel_visit(self.store, self.user, v["id"], "入力ミス")
        self.assertEqual(self.leave(v, v["guests"][1]).status_code, 409)

    def test_other_store_and_other_visit_ids_are_rejected(self):
        v = self.open(2)
        v2 = self.open(2, table=1)
        # 別の伝票の客 ID
        self.assertEqual(self.c.post(f"/api/visits/{v['id']}/guests/{v2['guests'][0]['id']}/leave", {}, format="json", **key()).status_code, 404)
        other = standard_fixture("s2", company_code="c2")
        oc = client_for(other.users["manager"], other.store)
        self.assertEqual(oc.post(f"/api/visits/{v['id']}/guests/{v['guests'][0]['id']}/leave", {}, format="json", **key()).status_code, 404)
        self.assertEqual(VisitGuest.objects.filter(status="left").count(), 0)

    def test_replay_does_not_error_or_double_apply(self):
        v = self.open(3)
        k = key()
        r1 = self.leave(v, v["guests"][0], **k)
        r2 = self.leave(v, v["guests"][0], **k)
        self.assertEqual((r1.status_code, r2.status_code), (200, 200))
        self.assertEqual(r2["Idempotent-Replayed"], "true")
        self.assertEqual(VisitGuest.objects.filter(visit_id=v["id"], status="left").count(), 1)

    def test_legacy_visit_without_guest_rows_keeps_counts(self):
        v = self.open(3, with_guests=False)
        self.assertEqual((v["guests"], v["present_count"], v["course_entries"][0]["present_count"]), ([], 3, 3))


class JoinTests(Base):
    def test_join_adds_guest_rows_when_visit_has_guests(self):
        v = self.open(2)
        r = self.c.post(f"/api/visits/{v['id']}/course-entries", {"guest_count": 1, "course_id": self.set60.id}, format="json", **key())
        self.assertEqual(r.status_code, 201, r.content)
        d = r.json()
        self.assertEqual((d["guest_count"], d["present_count"]), (3, 3))
        self.assertEqual([g["seq"] for g in d["guests"]], [1, 2, 3])
        self.assertEqual(d["guests"][2]["course_entry"], d["course_entries"][1]["id"])
        self.assertEqual(Decimal(d["quote"]["total"]), Decimal(10500))  # 3,500 × 3

    def test_join_with_designation(self):
        v = self.open(1)
        r = self.c.post(f"/api/visits/{v['id']}/course-entries",
                        {"guest_count": 1, "course_id": self.set60.id, "guests": [{"employee_id": self.cast1.id, "designation": "nomination"}]}, format="json", **key())
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()["guests"][1]["designation"], "nomination")
        self.assertEqual(Decimal(r.json()["quote"]["total"]), Decimal(3500 * 2 + 1000))

    def test_join_on_legacy_visit_creates_no_guest_rows(self):
        v = self.open(2, with_guests=False)
        r = self.c.post(f"/api/visits/{v['id']}/course-entries", {"guest_count": 1, "course_id": self.set60.id}, format="json", **key())
        self.assertEqual(r.json()["guests"], [])
        self.assertEqual(r.json()["guest_count"], 3)


class ExtensionOptionTests(Base):
    def ext(self, v, option_id=None, **kw):
        body = {} if option_id is None else {"option_id": option_id}
        return self.c.post(f"/api/visits/{v['id']}/course-entries/{v['course_entries'][0]['id']}/extend", body, format="json", **(kw or key()))

    def test_options_listed_with_amounts(self):
        v = self.open(2)
        e = v["course_entries"][0]
        opts = self.c.get(f"/api/visits/{v['id']}/course-entries/{e['id']}/extend-options").json()["options"]
        self.assertEqual([(o["option_id"], o["minutes"], Decimal(o["amount"])) for o in opts], [(self.opt30.id, 30, Decimal(4000)), (self.opt60.id, 60, Decimal(7000))])
        end = datetime.fromisoformat(e["planned_end_at"])
        self.assertEqual(datetime.fromisoformat(opts[0]["to_planned_end_at"]), end + timedelta(minutes=30))
        pv = self.c.get(f"/api/visits/{v['id']}/course-entries/{e['id']}/extend-preview?option_id={self.opt30.id}").json()
        self.assertEqual(Decimal(pv["amount"]), Decimal(4000))

    def test_course_without_options_has_single_legacy_candidate(self):
        v = self.open(2, course=self.fx.hourly, with_guests=False)
        e = v["course_entries"][0]
        opts = self.c.get(f"/api/visits/{v['id']}/course-entries/{e['id']}/extend-options").json()["options"]
        self.assertEqual([(o["option_id"], o["minutes"], Decimal(o["amount"])) for o in opts], [(None, 60, Decimal(5000))])

    def test_unlimited_course_has_no_options(self):
        v = self.open(1, course=self.fx.unlimited, with_guests=False)
        e = v["course_entries"][0]
        self.assertEqual(self.c.get(f"/api/visits/{v['id']}/course-entries/{e['id']}/extend-options").json()["options"], [])

    def test_extend_with_option_adds_from_planned_end_and_cancel_restores(self):
        v = self.open(2)
        e = v["course_entries"][0]
        end = datetime.fromisoformat(e["planned_end_at"])
        r = self.ext(v, self.opt30.id)
        self.assertEqual(r.status_code, 200, r.content)
        d = r.json()
        self.assertEqual(datetime.fromisoformat(d["course_entries"][0]["planned_end_at"]), end + timedelta(minutes=30))
        self.assertEqual(Decimal(d["quote"]["total"]), Decimal(7000 + 4000))
        line30 = d["created_line_id"]
        # 60 分を重ねる → さらに元の終了予定から足される
        d2 = self.ext(v, self.opt60.id).json()
        self.assertEqual(datetime.fromisoformat(d2["course_entries"][0]["planned_end_at"]), end + timedelta(minutes=90))
        self.assertEqual(Decimal(d2["quote"]["total"]), Decimal(7000 + 4000 + 7000))
        # 先に入れた 30 分のほうを取り消す → 30 分だけ戻る（候補ごとの分数を明細に残している）
        r = self.c.post(f"/api/visits/{v['id']}/lines/cancel-batch", {"line_ids": [line30], "reason": "入力ミス"}, format="json", **key())
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(datetime.fromisoformat(r.json()["course_entries"][0]["planned_end_at"]), end + timedelta(minutes=60))
        self.assertEqual(Decimal(r.json()["quote"]["total"]), Decimal(7000 + 7000))
        # 残りも取り消すと、終了予定と金額が元に戻る
        r = self.c.post(f"/api/visits/{v['id']}/lines/cancel-batch", {"line_ids": [d2["created_line_id"]], "reason": "入力ミス"}, format="json", **key())
        e3 = r.json()["course_entries"][0]
        self.assertEqual((e3["planned_end_at"], e3["extension_count"]), (e["planned_end_at"], 0))
        self.assertEqual(r.json()["quote"]["total"], v["quote"]["total"])

    def test_omitted_option_id_is_legacy_behaviour(self):
        v = self.open(2)
        e = v["course_entries"][0]
        d = self.ext(v).json()
        line = [l for l in d["lines"] if l["line_type"] == "extension"][0]
        # コースの extension_price・extension_minutes（セットに保存した値）
        self.assertEqual((Decimal(line["unit_price"]), Decimal(line["amount"])), (Decimal(3500), Decimal(7000)))
        self.assertEqual(datetime.fromisoformat(d["course_entries"][0]["planned_end_at"]), datetime.fromisoformat(e["planned_end_at"]) + timedelta(minutes=60))

    def test_option_of_other_course_or_store_is_rejected(self):
        v = self.open(2)
        other_course_opt = CourseExtensionOption.objects.create(course=self.fx.hourly, minutes=15, price=Decimal(1))
        other = make_store("s2", company_code="c2")
        other_store_opt = CourseExtensionOption.objects.create(course=other.hourly, minutes=15, price=Decimal(1))
        for oid in (other_course_opt.id, other_store_opt.id, 999999):
            self.assertEqual(self.ext(v, oid).status_code, 400, oid)
        self.assertFalse(OrderLine.objects.filter(line_type="extension").exists())

    def test_replay_does_not_extend_twice(self):
        v = self.open(2)
        k = key()
        r1 = self.ext(v, self.opt30.id, **k)
        r2 = self.ext(v, self.opt30.id, **k)
        self.assertEqual((r1.status_code, r2.status_code), (200, 200))
        self.assertEqual(OrderLine.objects.filter(visit_id=v["id"], line_type="extension").count(), 1)

    def test_master_change_does_not_touch_existing_lines(self):
        v = self.open(2)
        d = self.ext(v, self.opt30.id).json()
        self.opt30.price = Decimal(9999)
        self.opt30.minutes = 45
        self.opt30.save()
        line = OrderLine.objects.get(id=d["created_line_id"])
        self.assertEqual((line.unit_price, line.amount, line.extension_minutes), (Decimal(2000), Decimal(4000), 30))

    def test_course_api_replaces_options(self):
        r = self.owner.patch(f"/api/store/courses/{self.set60.id}", {"extension_options": [{"minutes": 20, "price": "1500"}, {"minutes": 40, "price": "2800"}]}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual([(o["minutes"], Decimal(o["price"])) for o in r.json()["extension_options"]], [(20, Decimal(1500)), (40, Decimal(2800))])
        # 候補を渡さない更新では変わらない
        r = self.owner.patch(f"/api/store/courses/{self.set60.id}", {"note": "x"}, format="json")
        self.assertEqual(len(r.json()["extension_options"]), 2)
        self.assertEqual(self.owner.patch(f"/api/store/courses/{self.set60.id}", {"extension_options": [{"minutes": 0, "price": "1"}]}, format="json").status_code, 400)

    def test_set_that_crosses_midnight(self):
        # 営業日をまたぐセット（23:20〜00:20）。開始時刻を直しても営業日は変わらず、延長は終了予定から足される
        v = self.open(2)
        e = v["course_entries"][0]
        from django.utils import timezone
        from apps.sales.models import CourseEntry

        local_now = timezone.localtime()
        start = (local_now - timedelta(days=1)).replace(hour=23, minute=20, second=0, microsecond=0)
        if local_now - start > timedelta(hours=23):
            start = local_now.replace(hour=23, minute=20, second=0, microsecond=0) - timedelta(days=1)
        CourseEntry.objects.filter(id=e["id"]).update(started_at=start, planned_end_at=start + timedelta(minutes=60))
        d = self.get(v["id"])
        end = datetime.fromisoformat(d["course_entries"][0]["planned_end_at"])
        self.assertEqual((timezone.localtime(end).hour, timezone.localtime(end).minute), (0, 20))
        self.assertNotEqual(timezone.localtime(end).date(), start.date())
        d2 = self.ext(v, self.opt30.id).json()
        end2 = timezone.localtime(datetime.fromisoformat(d2["course_entries"][0]["planned_end_at"]))
        self.assertEqual((end2.hour, end2.minute), (0, 50))
        self.assertEqual(d2["business_date"], v["business_date"])
