"""
セット料金は「注文」の明細として 1 人 1 行（1 組あたりの料金なら 1 行）で入れる。
- セットカット = セット料金の明細の取消。料金だけ消え、セットの枠（人数・時間・アラーム）は残る
- セットごと外す（途中合流などの入力ミス）= course-entries/<id>/remove。そのセットの明細をまとめて取り消し、人数からも外す
"""
from __future__ import annotations

import uuid
from decimal import Decimal

from rest_framework.test import APITestCase

from apps.core.testing import client_for, make_store, standard_fixture
from apps.sales import services
from apps.sales.models import CourseEntry, CourseEvent, OrderLine, Visit, VisitGuest


def key():
    return {"HTTP_IDEMPOTENCY_KEY": str(uuid.uuid4())}


class Base(APITestCase):
    def setUp(self):
        self.fx = standard_fixture()
        self.store = self.fx.store
        self.user = self.fx.users["manager"]
        self.c = client_for(self.user, self.store)
        self.course = self.fx.hourly  # 2,500 円・60 分・延長 2,500 円

    def open_visit(self, guests=2, **entry):
        r = self.c.post("/api/visits", {"table_id": self.fx.tables[0].id, "entries": [{"guest_count": guests, "course_id": self.course.id, **entry}]},
                        format="json", **key())
        self.assertEqual(r.status_code, 201, r.content)
        return r.json()

    def set_lines(self, vid, **kw):
        return list(OrderLine.objects.filter(visit_id=vid, line_type="course_set", **kw).order_by("id"))

    def total(self, vid):
        return Decimal(self.c.get(f"/api/visits/{vid}").json()["quote"]["total"])

    def cancel(self, vid, line_id, reason="セットカット"):
        return self.c.post(f"/api/visits/{vid}/lines/cancel-batch", {"line_ids": [line_id], "reason": reason}, format="json", **key())

    def remove(self, vid, entry_id, reason="入力ミス"):
        return self.c.post(f"/api/visits/{vid}/course-entries/{entry_id}/remove", {"reason": reason}, format="json", **key())


class SetLinesTests(Base):
    def test_one_line_per_person(self):
        v = self.open_visit(2)
        lines = self.set_lines(v["id"])
        self.assertEqual([(l.unit_price, l.quantity, l.amount) for l in lines], [(Decimal("2500"), Decimal("1"), Decimal("2500"))] * 2)
        self.assertEqual({l.course_entry_id for l in lines}, {v["course_entries"][0]["id"]})
        self.assertEqual(self.total(v["id"]), Decimal("5000"))

    def test_per_group_is_one_line(self):
        self.fx.settings.price_basis = "per_group"
        self.fx.settings.save()
        v = self.open_visit(3)
        self.assertEqual([l.amount for l in self.set_lines(v["id"])], [Decimal("2500")])
        self.assertEqual(self.total(v["id"]), Decimal("2500"))

    def test_charge_lines_belong_to_the_set(self):
        self.course.charge_amount = Decimal(500)
        self.course.save()
        v = self.open_visit(2, charge=True)
        charges = OrderLine.objects.filter(visit_id=v["id"], line_type="charge")
        self.assertEqual({c.course_entry_id for c in charges}, {v["course_entries"][0]["id"]})


class SetCutTests(Base):
    def test_cut_one_person_keeps_people_and_time(self):
        v = self.open_visit(2)
        before = CourseEntry.objects.get(id=v["course_entries"][0]["id"])
        r = self.cancel(v["id"], self.set_lines(v["id"])[0].id)
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertEqual(Decimal(body["quote"]["total"]), Decimal("2500"))
        self.assertEqual((body["guest_count"], body["present_count"]), (2, 2))
        e = CourseEntry.objects.get(id=before.id)
        self.assertTrue(e.is_active)
        self.assertEqual((e.planned_end_at, e.guest_count), (before.planned_end_at, 2))
        ev = CourseEvent.objects.get(course_entry=e, kind="set_cut")
        self.assertEqual((ev.amount, ev.note), (Decimal("2500"), "セットカット"))
        # ホールのアラーム・残り時間の対象のまま
        floor = self.c.get("/api/floor/state").json()
        self.assertTrue(any(ce["id"] == e.id and ce["is_active"] for t in floor["tables"] for vv in t["visits"] for ce in vv["course_entries"]))

    def test_cut_all_then_extend_and_checkout(self):
        v = self.open_visit(2)
        for l in self.set_lines(v["id"]):
            self.assertEqual(self.cancel(v["id"], l.id).status_code, 200)
        self.assertEqual(self.total(v["id"]), Decimal("0"))
        entry_id = v["course_entries"][0]["id"]
        # セットカットのあとも延長できる（延長は在席人数ぶん）
        r = self.c.post(f"/api/visits/{v['id']}/course-entries/{entry_id}/extend", {}, format="json", **key())
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(self.total(v["id"]), Decimal("5000"))
        co = services.confirm_checkout(self.store, self.user, v["id"], {"payments": [{"method": "cash", "amount": "5000", "tendered": "5000"}]})
        self.assertEqual(co.total, Decimal("5000"))
        self.assertEqual(Visit.objects.get(id=v["id"]).status, "closed")
        self.assertFalse(CourseEntry.objects.get(id=entry_id).is_active)

    def test_cut_while_extension_remains(self):
        """延長の明細が残っていても、セット料金だけ取り消せる（以前は延長を先に取り消す必要があった）"""
        v = self.open_visit(1)
        entry_id = v["course_entries"][0]["id"]
        self.c.post(f"/api/visits/{v['id']}/course-entries/{entry_id}/extend", {}, format="json", **key())
        r = self.cancel(v["id"], self.set_lines(v["id"])[0].id)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(self.total(v["id"]), Decimal("2500"))
        self.assertTrue(CourseEntry.objects.get(id=entry_id).is_active)

    def test_split_checkout_keeps_set_running_until_last_person(self):
        v = self.open_visit(2)
        entry_id = v["course_entries"][0]["id"]
        first, second = self.set_lines(v["id"])
        services.confirm_checkout(self.store, self.user, v["id"], {"line_ids": [first.id], "payments": [{"method": "cash", "amount": "2500", "tendered": "2500"}]})
        self.assertTrue(CourseEntry.objects.get(id=entry_id).is_active)  # もう 1 人のセットは続く
        services.confirm_checkout(self.store, self.user, v["id"], {"line_ids": [second.id], "payments": [{"method": "cash", "amount": "2500", "tendered": "2500"}]})
        self.assertFalse(CourseEntry.objects.get(id=entry_id).is_active)
        self.assertEqual(CourseEvent.objects.filter(course_entry_id=entry_id, kind="end").count(), 1)


class RemoveEntryTests(Base):
    def join(self, vid, guests=1, **extra):
        r = self.c.post(f"/api/visits/{vid}/course-entries", {"guest_count": guests, "course_id": self.course.id, **extra}, format="json", **key())
        self.assertEqual(r.status_code, 201, r.content)
        return CourseEntry.objects.filter(visit_id=vid).order_by("-id").first()

    def test_remove_joined_set(self):
        self.course.charge_amount = Decimal(500)
        self.course.save()
        v = self.open_visit(2)
        joined = self.join(v["id"], 2, charge=True)
        self.c.post(f"/api/visits/{v['id']}/course-entries/{joined.id}/extend", {}, format="json", **key())
        self.assertEqual(self.total(v["id"]), Decimal("16000"))  # 5,000 + 合流 5,000 + チャージ 1,000 + 延長 5,000
        r = self.remove(v["id"], joined.id)
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertEqual(Decimal(body["quote"]["total"]), Decimal("5000"))
        self.assertEqual(body["guest_count"], 2)
        self.assertFalse(CourseEntry.objects.get(id=joined.id).is_active)
        self.assertFalse(OrderLine.objects.filter(course_entry=joined, status="active").exists())
        self.assertTrue(all(l.cancel_reason.startswith("セットを外す") for l in OrderLine.objects.filter(course_entry=joined)))
        self.assertEqual(CourseEvent.objects.get(course_entry=joined, kind="remove").note, "入力ミス")
        # 操作履歴に日本語で残る
        from apps.core.audit_labels import action_label, changes_summary
        from apps.core.models import AuditLog

        log = AuditLog.objects.get(action="visit.remove_entry")
        self.assertEqual(action_label(log.action), "セットを外す")
        self.assertIn("理由 入力ミス", changes_summary(log.changes))
        # もとのセットはそのまま
        self.assertEqual(len(self.set_lines(v["id"], status="active")), 2)

    def test_remove_deletes_guest_rows_of_that_set(self):
        r = self.c.post("/api/visits", {"table_id": self.fx.tables[0].id, "entries": [{"guest_count": 1, "course_id": self.course.id}],
                                        "guests": [{"entry_index": 0}]}, format="json", **key())
        v = r.json()
        joined = self.join(v["id"], 2)
        self.assertEqual(self.c.get(f"/api/visits/{v['id']}").json()["present_count"], 3)
        body = self.remove(v["id"], joined.id).json()
        self.assertEqual((body["guest_count"], body["present_count"]), (1, 1))
        self.assertEqual(list(VisitGuest.objects.filter(visit_id=v["id"]).values_list("seq", flat=True)), [1])

    def test_last_set_cannot_be_removed(self):
        v = self.open_visit(2)
        r = self.remove(v["id"], v["course_entries"][0]["id"])
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["error"]["code"], "last_entry")
        self.assertTrue(CourseEntry.objects.get(id=v["course_entries"][0]["id"]).is_active)

    def test_reason_required_and_checked_out_set_refused(self):
        v = self.open_visit(1)
        joined = self.join(v["id"], 1)
        self.assertEqual(self.remove(v["id"], joined.id, reason="").status_code, 400)
        line = self.set_lines(v["id"], course_entry=joined)[0]
        services.confirm_checkout(self.store, self.user, v["id"], {"line_ids": [line.id], "payments": [{"method": "cash", "amount": "2500", "tendered": "2500"}]})
        r = self.remove(v["id"], joined.id)
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["error"]["code"], "entry_closed")  # 会計でセットが終わっている

    def test_other_store_ids_are_rejected(self):
        v = self.open_visit(1)
        self.join(v["id"], 1)
        other = make_store("s2", company_code="c2")
        ov = services.open_visit(other.store, self.user, {"table_id": other.tables[0].id, "entries": [{"guest_count": 1, "course_id": other.hourly.id},
                                                                                                     {"guest_count": 1, "course_id": other.hourly.id}]})
        oe = ov.course_entries.order_by("-id").first()
        # 別店舗の伝票 ID
        self.assertEqual(self.remove(ov.id, oe.id).status_code, 404)
        # 自店舗の伝票に別店舗のセット ID
        self.assertEqual(self.remove(v["id"], oe.id).status_code, 404)
        self.assertTrue(CourseEntry.objects.get(id=oe.id).is_active)
