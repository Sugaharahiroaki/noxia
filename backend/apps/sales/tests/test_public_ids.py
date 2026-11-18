"""伝票 ID・会計 ID（連番にしないランダムな英数字）。"""
from __future__ import annotations

import re
import uuid
from decimal import Decimal

from rest_framework.test import APITestCase

from unittest import mock

from apps.core.ids import ALPHABET, format_public_id, new_public_id
from apps.core.testing import client_for, standard_fixture
from apps.sales import services
from apps.sales.models import Checkout, Visit


def key():
    return {"HTTP_IDEMPOTENCY_KEY": str(uuid.uuid4())}


class PublicIdTests(APITestCase):
    def setUp(self):
        self.fx = standard_fixture()
        self.store = self.fx.store
        self.user = self.fx.users["manager"]
        self.c = client_for(self.user, self.store)

    def test_format(self):
        ids = {new_public_id() for _ in range(500)}
        self.assertEqual(len(ids), 500)
        for v in ids:
            self.assertEqual(len(v), 8)  # 2026-11-17 から 8 文字（それより前の ID は 12 文字のまま）
            self.assertTrue(set(v) <= set(ALPHABET))
        self.assertFalse(set("01IO") & set(ALPHABET))  # 見間違えやすい文字は使わない
        self.assertEqual(format_public_id("K7QM2XRA"), "K7QM-2XRA")
        self.assertEqual(format_public_id("K7QM2XRA9BHT"), "K7QM-2XRA-9BHT")  # 前からの 12 文字の ID

    def test_visit_and_checkout_get_random_ids_not_sequences(self):
        made = []
        for i in range(3):
            r = self.c.post("/api/visits", {"table_id": self.fx.tables[i].id, "entries": [{"guest_count": 1, "course_id": self.fx.hourly.id}]}, format="json", **key())
            made.append(r.json())
        pids = [v["public_id"] for v in made]
        self.assertEqual(len(set(pids)), 3)
        self.assertTrue(all(re.fullmatch(r"[2-9A-HJ-NP-Z]{8}", p) for p in pids), pids)
        self.assertEqual([v["slip_number"] for v in made], [1, 2, 3])  # 店舗内の通し番号（内部用）は今までどおり
        # 連番から推測できない（番号を含まない・並びが連番順でない保証はしないが、互いに無関係）
        self.assertFalse(any(str(v["slip_number"]) == v["public_id"] for v in made))
        co = services.confirm_checkout(self.store, self.user, made[0]["id"], {"payments": [{"method": "card", "amount": "2500"}]})
        self.assertRegex(co.public_id, r"^[2-9A-HJ-NP-Z]{8}$")
        self.assertNotEqual(co.public_id, pids[0])
        d = self.c.get(f"/api/checkouts/{co.id}").json()
        self.assertEqual((d["public_id"], d["visit_public_id"]), (co.public_id, pids[0]))

    def test_ids_in_floor_reports_and_csv(self):
        v = services.open_visit(self.store, self.user, {"table_id": self.fx.tables[0].id, "entries": [{"guest_count": 1, "course_id": self.fx.hourly.id}]})
        co = services.confirm_checkout(self.store, self.user, v.id, {"payments": [{"method": "cash", "amount": "2500", "tendered": "3000"}]})
        row = self.c.get("/api/reports/slips").json()["rows"][0]
        self.assertEqual(row["public_id"], v.public_id)
        detail = self.c.get(f"/api/reports/slips/{v.id}").json()
        self.assertEqual((detail["public_id"], detail["checkouts"][0]["public_id"]), (v.public_id, co.public_id))
        owner = client_for(self.fx.users["owner"], self.store)
        csv_text = owner.get("/api/exports/sales.csv").content.decode("utf-8-sig")
        header, first = csv_text.splitlines()[0], csv_text.splitlines()[1]
        self.assertIn("伝票ID", header)
        self.assertIn("会計ID", header)
        self.assertIn(format_public_id(v.public_id), first)
        self.assertIn(format_public_id(co.public_id), first)
        pay_text = owner.get("/api/exports/payments.csv").content.decode("utf-8-sig")
        self.assertIn(format_public_id(co.public_id), pay_text)

    def test_ids_are_unique_in_db(self):
        v = services.open_visit(self.store, self.user, {"table_id": self.fx.tables[0].id, "entries": [{"guest_count": 1, "course_id": self.fx.hourly.id}]})
        v2 = services.open_visit(self.store, self.user, {"table_id": self.fx.tables[1].id, "entries": [{"guest_count": 1, "course_id": self.fx.hourly.id}]})
        from django.db import IntegrityError, transaction

        # DB でも一意（保存の処理を通らない書き換えも止まる）
        with self.assertRaises(IntegrityError), transaction.atomic():
            Visit.objects.filter(id=v2.id).update(public_id=v.public_id)
        self.assertEqual(Checkout.objects.count(), 0)

    def test_collision_is_regenerated(self):
        # 8 文字にしたので、新しく作った ID が既存とぶつかったら作り直して保存する（エラーにしない）
        v = services.open_visit(self.store, self.user, {"table_id": self.fx.tables[0].id, "entries": [{"guest_count": 1, "course_id": self.fx.hourly.id}]})
        # 作り直しの 1 回目もぶつかり、2 回目で空いている ID になる
        with mock.patch("apps.core.ids.new_public_id", side_effect=[v.public_id, "ZZZZ2222"]):
            v2 = Visit(store=self.store, business_date=v.business_date, slip_number=999, table=self.fx.tables[1], opened_at=v.opened_at, public_id=v.public_id)
            v2.save()
        self.assertEqual(Visit.objects.get(id=v2.id).public_id, "ZZZZ2222")
        self.assertEqual(Visit.objects.filter(public_id=v.public_id).count(), 1)
        # 会計も同じ
        co = services.confirm_checkout(self.store, self.user, v.id, {"payments": [{"method": "card", "amount": "2500"}]})
        with mock.patch("apps.core.ids.new_public_id", return_value="YYYY3333"):
            co2 = Checkout(store=self.store, visit=v2, number=999, business_date=v.business_date, confirmed_at=co.confirmed_at,
                           subtotal=Decimal(0), total_before_rounding=Decimal(0), total=Decimal(0), total_excl_tax=Decimal(0), public_id=co.public_id)
            co2.save()
        self.assertEqual(Checkout.objects.get(id=co2.id).public_id, "YYYY3333")

    def test_screens_and_slips_show_ids_not_numbers(self):
        # 画面・印刷・メッセージには、連番（伝票番号・会計番号）ではなく ID を出す
        v = services.open_visit(self.store, self.user, {"table_id": self.fx.tables[0].id, "entries": [{"guest_count": 1, "course_id": self.fx.hourly.id}]})
        vid = format_public_id(v.public_id)
        self.assertEqual(str(v), f"伝票 {vid}")
        est_text = str(self.c.get(f"/api/visits/{v.id}/print/estimate").json()["slip"])
        self.assertIn(f"伝票 {vid}", est_text)
        self.assertNotIn("伝票#", est_text)
        # 卓一覧の伝票にも ID が付いている
        floor = self.c.get("/api/floor/state").json()
        self.assertIn(f"'public_id': '{v.public_id}'", str(floor))
        # 未会計の伝票があると締められない。そのときの案内も ID
        r = self.c.post("/api/cash/close", {"counted_cash": "0", "next_day_float": "0"}, format="json", **key())
        self.assertEqual(r.status_code, 409, r.content)
        self.assertIn(f"伝票 {vid}", r.json()["error"]["message"])
        co = services.confirm_checkout(self.store, self.user, v.id, {"payments": [{"method": "cash", "amount": "2500", "tendered": "3000"}]})
        cid = format_public_id(co.public_id)
        rec_text = str(self.c.get(f"/api/checkouts/{co.id}/print/receipt").json()["slip"])
        self.assertIn(f"伝票 {vid}", rec_text)
        self.assertIn(f"会計 {cid}", rec_text)
        self.assertNotIn("#", rec_text)
        self.assertIn(f"No.{cid}", str(self.c.get(f"/api/checkouts/{co.id}/print/ryoshusho").json()["slip"]))
        hw = self.c.get(f"/api/checkouts/{co.id}/handwrite").json()
        self.assertEqual((hw["visit_public_id"], hw["checkout_public_id"]), (v.public_id, co.public_id))
