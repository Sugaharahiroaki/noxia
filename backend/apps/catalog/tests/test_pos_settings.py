"""T6: POS 設定。セット構成の複製、商品の略称、マスタを変えても既存の伝票の明細が変わらないこと。"""
from __future__ import annotations

import uuid
from decimal import Decimal

from django.utils import timezone
from rest_framework.test import APITestCase

from apps.catalog.models import Course, CourseExtensionOption
from apps.core.testing import client_for, make_store, standard_fixture
from apps.sales.models import OrderLine
from apps.staff.models import BackEntry, BackRule

PAST = timezone.localdate().replace(year=2020)


def key():
    return {"HTTP_IDEMPOTENCY_KEY": str(uuid.uuid4())}


class Base(APITestCase):
    def setUp(self):
        self.fx = standard_fixture()
        self.store = self.fx.store
        self.owner = client_for(self.fx.users["owner"], self.store)
        self.manager = client_for(self.fx.users["manager"], self.store)
        self.staff = client_for(self.fx.users["staff"], self.store)
        self.set60 = Course.objects.create(store=self.store, name="セット60分", price=Decimal(3500), duration_minutes=60, extension_price=Decimal(3500), kind="セット", sort_order=3)
        CourseExtensionOption.objects.create(course=self.set60, minutes=30, price=Decimal(2000), sort_order=1)
        CourseExtensionOption.objects.create(course=self.set60, minutes=60, price=Decimal(3500), sort_order=2)


class CourseCopyTests(Base):
    def test_copy_duplicates_course_with_extension_options(self):
        r = self.manager.post(f"/api/store/courses/{self.set60.id}/copy", {}, format="json", **key())
        self.assertEqual(r.status_code, 201, r.content)
        d = r.json()
        self.assertNotEqual(d["id"], self.set60.id)
        self.assertEqual(d["name"], "セット60分（コピー）")
        self.assertEqual((Decimal(d["price"]), d["duration_minutes"], d["kind"], d["is_active"]), (Decimal(3500), 60, "セット", True))
        self.assertEqual([(o["minutes"], Decimal(o["price"])) for o in d["extension_options"]], [(30, Decimal(2000)), (60, Decimal(3500))])
        # 延長候補は別の行として複製される（複製を変えても元は変わらない）
        r2 = self.manager.patch(f"/api/store/courses/{d['id']}", {"price": "4000", "extension_options": [{"minutes": 30, "price": "2500"}]}, format="json")
        self.assertEqual(r2.status_code, 200, r2.content)
        self.set60.refresh_from_db()
        self.assertEqual(self.set60.price, Decimal(3500))
        self.assertEqual(self.set60.extension_options.count(), 2)

    def test_copy_twice_gets_distinct_names_and_replay_does_not_duplicate(self):
        k = key()
        r1 = self.manager.post(f"/api/store/courses/{self.set60.id}/copy", {}, format="json", **k)
        r1b = self.manager.post(f"/api/store/courses/{self.set60.id}/copy", {}, format="json", **k)
        self.assertEqual(r1b["Idempotent-Replayed"], "true")
        self.assertEqual(Course.objects.filter(store=self.store, name__startswith="セット60分（コピー）").count(), 1)
        r2 = self.manager.post(f"/api/store/courses/{self.set60.id}/copy", {}, format="json", **key())
        self.assertEqual((r1.json()["name"], r2.json()["name"]), ("セット60分（コピー）", "セット60分（コピー）2"))

    def test_copy_permissions_and_other_store(self):
        # 書き込みは catalog.edit（スタッフは不可）
        self.assertEqual(self.staff.post(f"/api/store/courses/{self.set60.id}/copy", {}, format="json", **key()).status_code, 403)
        other = make_store("s2", company_code="c2")
        self.assertEqual(self.manager.post(f"/api/store/courses/{other.hourly.id}/copy", {}, format="json", **key()).status_code, 404)
        self.assertFalse(Course.objects.filter(name__contains="（コピー）").exists())

    def test_copied_course_appears_as_entry_candidate(self):
        d = self.manager.post(f"/api/store/courses/{self.set60.id}/copy", {}, format="json", **key()).json()
        self.manager.patch(f"/api/store/courses/{d['id']}", {"name": "セット60分 週末", "price": "4000"}, format="json")
        names = {c["name"]: Decimal(c["price"]) for c in self.staff.get("/api/store/courses?active=1").json()}
        self.assertEqual(names["セット60分 週末"], Decimal(4000))
        self.assertEqual(names["セット60分"], Decimal(3500))


class ShortNameTests(Base):
    def test_product_short_name(self):
        p = self.fx.products["champagne"]
        r = self.manager.patch(f"/api/store/products/{p.id}", {"short_name": "シャン"}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        rows = {x["id"]: x for x in self.staff.get("/api/store/products?active=1").json()}
        self.assertEqual(rows[p.id]["short_name"], "シャン")
        self.assertEqual(rows[self.fx.products["beer"].id]["short_name"], "")  # 既存の商品は空のまま


class MasterChangeDoesNotTouchExistingLinesTests(Base):
    def test_existing_lines_keep_price_tax_and_back_conditions(self):
        cast1 = self.fx.employees["cast1"]
        drink = self.fx.products["cast_drink"]
        rule = BackRule.objects.create(store=self.store, name="cd 50%", target_type="category_kind", category_kind="cast_drink", calc_type="percent", value=Decimal(50), effective_from=PAST)
        v = self.manager.post("/api/visits", {"table_id": self.fx.tables[0].id, "entries": [{"guest_count": 2, "course_id": self.set60.id}], "guests": [{}, {}]}, format="json", **key()).json()
        r = self.manager.post(f"/api/visits/{v['id']}/lines/batch", {"items": [{"kind": "product", "product_id": drink.id, "quantity": 2, "back_targets": [{"employee_id": cast1.id}]}]}, format="json", **key())
        total_before = r.json()["quote"]["total"]
        line = OrderLine.objects.get(id=r.json()["created_line_ids"][0])
        back = BackEntry.objects.get(order_line=line)
        snap = (line.name, line.unit_price, line.amount, line.tax_rate, back.amount, back.rule_snapshot["value"])

        # マスタを変える（商品の名前・価格・税率・略称、コースの料金・時間・延長候補、卓の名前と色、バックの条件、カテゴリー）
        self.assertEqual(self.manager.patch(f"/api/store/products/{drink.id}", {"name": "CD 改", "short_name": "CD", "price": "9999", "tax_rate": "8.00"}, format="json").status_code, 200)
        self.assertEqual(self.manager.patch(f"/api/store/courses/{self.set60.id}", {"price": "9999", "duration_minutes": 90, "extension_options": [{"minutes": 15, "price": "999"}]}, format="json").status_code, 200)
        self.assertEqual(self.manager.patch(f"/api/store/tables/{self.fx.tables[0].id}", {"name": "VIP", "color": "#112233", "kind": "BOX"}, format="json").status_code, 200)
        self.assertEqual(self.manager.patch(f"/api/store/categories/{self.fx.categories['cast_drink'].id}", {"name": "レディース"}, format="json").status_code, 200)
        rule.value = Decimal(10)
        rule.save()

        d = self.manager.get(f"/api/visits/{v['id']}").json()
        self.assertEqual(d["quote"]["total"], total_before)  # 営業中の伝票の金額は変わらない
        line.refresh_from_db()
        back.refresh_from_db()
        self.assertEqual((line.name, line.unit_price, line.amount, line.tax_rate, back.amount, back.rule_snapshot["value"]), snap)
        set_line = OrderLine.objects.get(visit_id=v["id"], line_type="course_set")
        self.assertEqual((set_line.unit_price, set_line.amount), (Decimal(3500), Decimal(7000)))
        e = d["course_entries"][0]
        self.assertEqual(Decimal(e["course_price"]), Decimal(3500))
        self.assertEqual(d["table_name"], "VIP")  # 卓の名前は表示なので新しい名前になる

        # 次の新規伝票・注文には新しい値が反映される
        v2 = self.manager.post("/api/visits", {"table_id": self.fx.tables[1].id, "entries": [{"guest_count": 1, "course_id": self.set60.id}]}, format="json", **key()).json()
        self.assertEqual(Decimal(v2["quote"]["total"]), Decimal(9999))
        r2 = self.manager.post(f"/api/visits/{v2['id']}/lines/batch", {"items": [{"kind": "product", "product_id": drink.id, "quantity": 1}]}, format="json", **key())
        new_line = OrderLine.objects.get(id=r2.json()["created_line_ids"][0])
        self.assertEqual((new_line.name, new_line.unit_price, new_line.tax_rate), ("CD 改", Decimal(9999), Decimal("8.00")))

    def test_store_settings_save_still_requires_reauth(self):
        no_reauth = client_for(self.fx.users["manager"], self.store, reauth=False)
        r = no_reauth.put("/api/store/settings", {"jounai_fee": "1500"}, format="json")
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()["error"]["code"], "reauth_required")
        r = self.manager.put("/api/store/settings", {"jounai_fee": "1500", "alarm_mode": "10"}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual((Decimal(r.json()["jounai_fee"]), r.json()["alarm_mode"]), (Decimal(1500), "10"))
        # スタッフ（settings.edit なし）は保存できない
        self.assertEqual(self.staff.put("/api/store/settings", {"alarm_mode": "5"}, format="json").status_code, 403)
