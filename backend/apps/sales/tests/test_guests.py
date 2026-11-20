"""T2: 客ごとの指定つきの入店（客の行）、場内指名、入店前の見積。"""
from __future__ import annotations

import uuid
from decimal import Decimal

from django.utils import timezone
from rest_framework.test import APITestCase

from apps.catalog.models import Course
from apps.core.testing import client_for, make_store, add_employee, standard_fixture
from apps.sales import services
from apps.sales.models import OrderLine, Visit, VisitGuest
from apps.staff.models import BackEntry, BackRule

PAST = timezone.localdate().replace(year=2020)


def key():
    return {"HTTP_IDEMPOTENCY_KEY": str(uuid.uuid4())}


class Base(APITestCase):
    def setUp(self):
        # テスト用の料金（60 分 3,500 円・1 人単位、本指名 1,000・場内 800・同伴 2,000）。本番の値ではない
        self.fx = standard_fixture(jounai_fee=Decimal(800))
        self.store = self.fx.store
        self.user = self.fx.users["manager"]
        self.c = client_for(self.user, self.store)
        self.cast1 = self.fx.employees["cast1"]
        self.cast2 = self.fx.employees["cast2"]
        self.set60 = Course.objects.create(store=self.store, name="セット60分", price=Decimal(3500), duration_minutes=60, extension_price=Decimal(3500), kind="セット")

    def body(self, guests, entries=None, **extra):
        return {"table_id": self.fx.tables[0].id, "entries": entries or [{"guest_count": len(guests), "course_id": self.set60.id}], "guests": guests, **extra}

    def post(self, body, **kw):
        return self.c.post("/api/visits", body, format="json", **(kw or key()))


class OpenVisitWithGuestsTests(Base):
    def test_creates_guest_rows_and_fee_lines_in_one_go(self):
        BackRule.objects.create(store=self.store, name="本指名", target_type="nomination", calc_type="fixed", value=Decimal(500), effective_from=PAST)
        BackRule.objects.create(store=self.store, name="場内", target_type="jounai", calc_type="fixed", value=Decimal(300), effective_from=PAST)
        r = self.post(self.body([
            {"employee_id": self.cast1.id, "designation": "nomination"},
            {"employee_id": self.cast2.id, "designation": "jounai"},
        ]))
        self.assertEqual(r.status_code, 201, r.content)
        v = r.json()
        self.assertEqual(v["guest_count"], 2)
        self.assertEqual([(g["seq"], g["employee_name"], g["designation"], g["status"]) for g in v["guests"]],
                         [(1, "cast1", "nomination", "present"), (2, "cast2", "jounai", "present")])
        by_type = {l["line_type"]: l for l in v["lines"]}
        self.assertEqual(set(by_type), {"course_set", "nomination", "jounai"})
        # セット料金は 1 人 1 行（3,500 × 2 人 = 2 行）
        self.assertEqual([Decimal(l["amount"]) for l in v["lines"] if l["line_type"] == "course_set"], [Decimal(3500)] * 2)
        self.assertEqual(Decimal(by_type["nomination"]["amount"]), Decimal(1000))
        self.assertEqual(Decimal(by_type["jounai"]["amount"]), Decimal(800))
        self.assertEqual(by_type["jounai"]["line_type_label"], "場内指名")
        self.assertEqual(by_type["nomination"]["line_type_label"], "本指名")
        self.assertIn("場内指名料", by_type["jounai"]["name"])
        self.assertEqual(Decimal(v["quote"]["total"]), Decimal(8800))
        # 客の行は自分の明細とセットを指している
        g1, g2 = VisitGuest.objects.filter(visit_id=v["id"]).order_by("seq")
        self.assertEqual(g1.fee_line.line_type, "nomination")
        self.assertEqual(g2.fee_line.line_type, "jounai")
        self.assertEqual(g1.course_entry_id, v["course_entries"][0]["id"])
        # バックは種別ごとのルールで付く（場内は場内のルール）
        self.assertEqual({(b.kind, b.employee_id, b.amount) for b in BackEntry.objects.filter(visit_id=v["id"])},
                         {("nomination", self.cast1.id, Decimal(500)), ("jounai", self.cast2.id, Decimal(300))})

    def test_guest_without_designation_makes_no_fee_line(self):
        r = self.post(self.body([{"employee_id": self.cast1.id}, {}]))
        self.assertEqual(r.status_code, 201, r.content)
        v = r.json()
        self.assertEqual([l["line_type"] for l in v["lines"]], ["course_set", "course_set"])
        self.assertEqual([g["employee_name"] for g in v["guests"]], ["cast1", None])
        self.assertEqual(Decimal(v["quote"]["total"]), Decimal(7000))

    def test_legacy_request_without_guests_is_unchanged(self):
        r = self.post({"table_id": self.fx.tables[0].id, "entries": [{"guest_count": 3, "course_id": self.fx.hourly.id}]})
        self.assertEqual(r.status_code, 201, r.content)
        v = r.json()
        self.assertEqual(v["guests"], [])
        self.assertEqual(v["guest_count"], 3)
        self.assertEqual(Decimal(v["quote"]["total"]), Decimal(7500))
        self.assertFalse(VisitGuest.objects.exists())

    def test_per_guest_courses(self):
        r = self.post(self.body(
            [{"entry_index": 0, "employee_id": self.cast1.id, "designation": "douhan"}, {"entry_index": 1}],
            entries=[{"guest_count": 1, "course_id": self.set60.id}, {"guest_count": 1, "course_id": self.fx.hourly.id}],
        ))
        self.assertEqual(r.status_code, 201, r.content)
        v = r.json()
        self.assertEqual([g["course_entry"] for g in v["guests"]], [e["id"] for e in v["course_entries"]])
        self.assertEqual(Decimal(v["quote"]["total"]), Decimal(3500 + 2500 + 2000))

    def test_unset_fee_is_rejected_and_nothing_is_created(self):
        self.fx.settings.jounai_fee = None
        self.fx.settings.save()
        r = self.post(self.body([{"employee_id": self.cast1.id, "designation": "jounai"}]))
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["error"]["code"], "fee_unset")
        self.assertFalse(Visit.objects.exists())
        self.assertFalse(OrderLine.objects.exists())
        # 既存の指名（本指名）は料金が設定済みなので通る
        self.assertEqual(self.post(self.body([{"employee_id": self.cast1.id, "designation": "nomination"}])).status_code, 201)

    def test_validation(self):
        bad = [
            self.body([{"designation": "nomination"}]),  # キャストなしの指定
            self.body([{"employee_id": self.cast1.id, "designation": "vip"}]),  # 不明な指定
            self.body([{}], entries=[{"guest_count": 2, "course_id": self.set60.id}]),  # 人数が合わない
            self.body([{"entry_index": 5}]),  # セットの指定が範囲外
            self.body([{"employee_id": 999999, "designation": "nomination"}]),
        ]
        for b in bad:
            self.assertEqual(self.post(b).status_code, 400, b)
        self.assertFalse(Visit.objects.exists())

    def test_other_company_cast_is_rejected(self):
        other = standard_fixture("s2", company_code="c2")
        r = self.post(self.body([{"employee_id": other.employees["cast1"].id, "designation": "nomination"}]))
        self.assertEqual(r.status_code, 400)
        self.assertFalse(Visit.objects.exists())
        self.assertFalse(VisitGuest.objects.exists())

    def test_replay_does_not_create_second_visit(self):
        k = key()
        b = self.body([{"employee_id": self.cast1.id, "designation": "nomination"}, {}])
        r1 = self.post(b, **k)
        r2 = self.post(b, **k)
        self.assertEqual((r1.status_code, r2.status_code), (201, 201))
        self.assertEqual(r2["Idempotent-Replayed"], "true")
        self.assertEqual(Visit.objects.count(), 1)
        self.assertEqual(VisitGuest.objects.count(), 2)
        self.assertEqual(OrderLine.objects.filter(line_type="nomination").count(), 1)

    def test_floor_state_counts_guest_cast_as_engaged(self):
        self.post(self.body([{"employee_id": self.cast1.id}]))
        self.assertEqual(self.c.get("/api/floor/state").json()["engaged_employee_ids"], [self.cast1.id])


class JounaiLineTests(Base):
    def open(self):
        return self.post({"table_id": self.fx.tables[0].id, "entries": [{"guest_count": 1, "course_id": self.set60.id}]}).json()

    def test_add_jounai_line_later_and_cancel(self):
        BackRule.objects.create(store=self.store, name="場内", target_type="jounai", calc_type="fixed", value=Decimal(300), effective_from=PAST)
        v = self.open()
        r = self.c.post(f"/api/visits/{v['id']}/lines", {"kind": "jounai", "employee_id": self.cast1.id}, format="json", **key())
        self.assertEqual(r.status_code, 201, r.content)
        line = [l for l in r.json()["lines"] if l["line_type"] == "jounai"][0]
        self.assertEqual(Decimal(line["amount"]), Decimal(800))
        self.assertEqual(Decimal(r.json()["quote"]["total"]), Decimal(4300))
        self.assertEqual(Decimal(r.json()["quote"]["breakdown"]["jounai"]), Decimal(800))
        # 会計で場内のバックが確定する
        co = services.confirm_checkout(self.store, self.user, v["id"], {"payments": [{"method": "cash", "amount": "4300", "tendered": "5000"}]})
        b = BackEntry.objects.get(checkout=co)
        self.assertEqual((b.kind, b.status, b.amount), ("jounai", "confirmed", Decimal(300)))

    def test_jounai_fee_unset_rejects(self):
        self.fx.settings.jounai_fee = None
        self.fx.settings.save()
        v = self.open()
        r = self.c.post(f"/api/visits/{v['id']}/lines", {"kind": "jounai", "employee_id": self.cast1.id}, format="json", **key())
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["error"]["code"], "fee_unset")

    def test_existing_nomination_meaning_is_unchanged(self):
        v = self.open()
        r = self.c.post(f"/api/visits/{v['id']}/lines", {"kind": "nomination", "employee_id": self.cast1.id}, format="json", **key())
        line = [l for l in r.json()["lines"] if l["line_type"] == "nomination"][0]
        self.assertEqual((line["name"], Decimal(line["amount"])), ("指名料（cast1）", Decimal(1000)))

    def test_back_targets_can_be_changed_on_jounai(self):
        v = self.open()
        r = self.c.post(f"/api/visits/{v['id']}/lines", {"kind": "jounai", "employee_id": self.cast1.id}, format="json", **key())
        line = [l for l in r.json()["lines"] if l["line_type"] == "jounai"][0]
        r = self.c.put(f"/api/visits/{v['id']}/lines/{line['id']}/back-targets", {"back_targets": [{"employee_id": self.cast2.id}]}, format="json")
        self.assertEqual(r.status_code, 200, r.content)


class PreviewTests(Base):
    def test_preview_matches_created_visit_and_saves_nothing(self):
        b = self.body([{"employee_id": self.cast1.id, "designation": "nomination"}, {}])
        p = self.c.post("/api/visits/preview", b, format="json")
        self.assertEqual(p.status_code, 200, p.content)
        self.assertFalse(Visit.objects.exists())
        self.assertEqual([(l["line_type"], Decimal(l["amount"])) for l in p.json()["lines"]], [("course_set", Decimal(7000)), ("nomination", Decimal(1000))])
        v = self.post(b).json()
        self.assertEqual(p.json()["quote"]["total"], v["quote"]["total"])
        self.assertEqual(p.json()["quote"]["subtotal"], v["quote"]["subtotal"])

    def test_preview_uses_store_settings(self):
        self.fx.settings.service_charge_enabled = True
        self.fx.settings.service_charge_rate = Decimal("10")
        self.fx.settings.save()
        p = self.c.post("/api/visits/preview", self.body([{}, {}]), format="json").json()
        self.assertEqual(Decimal(p["quote"]["total"]), Decimal(7700))  # 7,000 + サービス料 10%（テスト用の設定）

    def test_preview_rejects_other_store_course_and_unset_fee(self):
        other = make_store("s2", company_code="c2")
        r = self.c.post("/api/visits/preview", {"entries": [{"guest_count": 1, "course_id": other.hourly.id}]}, format="json")
        self.assertEqual(r.status_code, 400)
        self.fx.settings.douhan_fee = None
        self.fx.settings.save()
        r = self.c.post("/api/visits/preview", self.body([{"employee_id": self.cast1.id, "designation": "douhan"}]), format="json")
        self.assertEqual(r.status_code, 409)

    def test_cast_role_cannot_preview(self):
        self.assertEqual(client_for(self.fx.users["cast"], self.store).post("/api/visits/preview", self.body([{}]), format="json").status_code, 403)


class CourseKindTests(Base):
    def test_course_kind_is_served_and_editable(self):
        owner = client_for(self.fx.users["owner"], self.store)
        rows = {c["name"]: c["kind"] for c in owner.get("/api/store/courses?active=1").json()}
        self.assertEqual(rows["セット60分"], "セット")
        self.assertEqual(rows[self.fx.hourly.name], "")  # 既存のコースは区分なしのまま
        r = owner.patch(f"/api/store/courses/{self.fx.hourly.id}", {"kind": "飲み放題"}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
