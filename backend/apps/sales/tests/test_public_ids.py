"""伝票 ID・会計 ID（連番にしないランダムな英数字）。"""
from __future__ import annotations

import re
import uuid
from decimal import Decimal

from rest_framework.test import APITestCase

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
            self.assertEqual(len(v), 12)
            self.assertTrue(set(v) <= set(ALPHABET))
        self.assertFalse(set("01IO") & set(ALPHABET))  # 見間違えやすい文字は使わない
        self.assertEqual(format_public_id("K7QM2XRA9BHT"), "K7QM-2XRA-9BHT")

    def test_visit_and_checkout_get_random_ids_not_sequences(self):
        made = []
        for i in range(3):
            r = self.c.post("/api/visits", {"table_id": self.fx.tables[i].id, "entries": [{"guest_count": 1, "course_id": self.fx.hourly.id}]}, format="json", **key())
            made.append(r.json())
        pids = [v["public_id"] for v in made]
        self.assertEqual(len(set(pids)), 3)
        self.assertTrue(all(re.fullmatch(r"[2-9A-HJ-NP-Z]{12}", p) for p in pids), pids)
        self.assertEqual([v["slip_number"] for v in made], [1, 2, 3])  # 店舗内の通し番号（内部用）は今までどおり
        # 連番から推測できない（番号を含まない・並びが連番順でない保証はしないが、互いに無関係）
        self.assertFalse(any(str(v["slip_number"]) == v["public_id"] for v in made))
        co = services.confirm_checkout(self.store, self.user, made[0]["id"], {"payments": [{"method": "card", "amount": "2500"}]})
        self.assertRegex(co.public_id, r"^[2-9A-HJ-NP-Z]{12}$")
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
        from django.db import IntegrityError, transaction

        with self.assertRaises(IntegrityError), transaction.atomic():
            Visit.objects.create(store=self.store, business_date=v.business_date, slip_number=999, table=self.fx.tables[1], opened_at=v.opened_at, public_id=v.public_id)
        self.assertEqual(Checkout.objects.count(), 0)
        self.assertEqual(Decimal(1), Decimal(1))
