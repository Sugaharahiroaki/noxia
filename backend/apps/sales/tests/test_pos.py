"""POS 操作向け: カートの一括注文・直前注文の一括取消・未会計があると日締め不可。"""
from __future__ import annotations

import uuid
from decimal import Decimal

from django.utils import timezone
from rest_framework.test import APITestCase

from apps.core.testing import client_for, make_store, standard_fixture
from apps.inventory.models import StockMovement
from apps.sales import services
from apps.sales.models import OrderLine, Visit
from apps.staff.models import BackEntry, BackRule


def key():
    return {"HTTP_IDEMPOTENCY_KEY": str(uuid.uuid4())}


class PosBase(APITestCase):
    def setUp(self):
        self.fx = standard_fixture()
        self.store = self.fx.store
        self.c = client_for(self.fx.users["manager"], self.store)
        self.p = self.fx.products
        self.cast1 = self.fx.employees["cast1"]
        BackRule.objects.create(store=self.store, name="cast drink 50%", target_type="category_kind", category_kind="cast_drink",
                                calc_type="percent", value=Decimal(50), effective_from=timezone.localdate().replace(year=2020))
        StockMovement.objects.create(store=self.store, product=self.p["champagne"], kind="receive", quantity=Decimal(6), occurred_at=timezone.now())

    def open_visit(self):
        r = self.c.post("/api/visits", {"table_id": self.fx.tables[0].id, "entries": [{"guest_count": 2, "course_id": self.fx.hourly.id}]}, format="json", **key())
        self.assertEqual(r.status_code, 201, r.content)
        return r.json()

    def stock(self, product):
        return sum((m.quantity for m in StockMovement.objects.filter(product=product)), Decimal(0))


class BatchOrderTests(PosBase):
    def test_batch_adds_all_items_at_once(self):
        v = self.open_visit()
        items = [
            {"kind": "product", "product_id": self.p["beer"].id, "quantity": 3},
            {"kind": "product", "product_id": self.p["cast_drink"].id, "quantity": 2, "back_targets": [{"employee_id": self.cast1.id}]},
            {"kind": "product", "product_id": self.p["champagne"].id, "quantity": 1},
        ]
        r = self.c.post(f"/api/visits/{v['id']}/lines/batch", {"items": items}, format="json", **key())
        self.assertEqual(r.status_code, 201, r.content)
        data = r.json()
        self.assertEqual(len(data["created_line_ids"]), 3)
        # セット 2,500×2 + ビール 800×3 + キャストドリンク 1,500×2 + シャンパン 30,000
        self.assertEqual(Decimal(data["quote"]["total"]), Decimal(5000 + 2400 + 3000 + 30000))
        self.assertEqual(self.stock(self.p["champagne"]), Decimal(5))
        self.assertEqual(BackEntry.objects.filter(employee=self.cast1).count(), 1)

    def test_batch_is_atomic_when_one_item_is_invalid(self):
        v = self.open_visit()
        other = make_store("s2", company_code="c2")
        items = [
            {"kind": "product", "product_id": self.p["champagne"].id, "quantity": 1},
            {"kind": "product", "product_id": other.products["beer"].id, "quantity": 1},  # 別会社の商品
        ]
        r = self.c.post(f"/api/visits/{v['id']}/lines/batch", {"items": items}, format="json", **key())
        self.assertEqual(r.status_code, 400)
        self.assertEqual(OrderLine.objects.filter(visit_id=v["id"], line_type="product").count(), 0)
        self.assertEqual(self.stock(self.p["champagne"]), Decimal(6))  # 在庫も減っていない

    def test_batch_replay_does_not_duplicate(self):
        v = self.open_visit()
        k = key()
        body = {"items": [{"kind": "product", "product_id": self.p["beer"].id, "quantity": 1}]}
        r1 = self.c.post(f"/api/visits/{v['id']}/lines/batch", body, format="json", **k)
        r2 = self.c.post(f"/api/visits/{v['id']}/lines/batch", body, format="json", **k)
        self.assertEqual(r1.status_code, 201)
        self.assertEqual(r2.status_code, 201)
        self.assertEqual(r2["Idempotent-Replayed"], "true")
        self.assertEqual(OrderLine.objects.filter(visit_id=v["id"], line_type="product").count(), 1)

    def test_batch_requires_items_and_limits_size(self):
        v = self.open_visit()
        self.assertEqual(self.c.post(f"/api/visits/{v['id']}/lines/batch", {"items": []}, format="json", **key()).status_code, 400)
        many = [{"kind": "product", "product_id": self.p["beer"].id, "quantity": 1}] * 51
        self.assertEqual(self.c.post(f"/api/visits/{v['id']}/lines/batch", {"items": many}, format="json", **key()).status_code, 400)

    def test_cancel_batch_undoes_last_order(self):
        v = self.open_visit()
        items = [
            {"kind": "product", "product_id": self.p["champagne"].id, "quantity": 1},
            {"kind": "product", "product_id": self.p["cast_drink"].id, "quantity": 1, "back_targets": [{"employee_id": self.cast1.id}]},
        ]
        r = self.c.post(f"/api/visits/{v['id']}/lines/batch", {"items": items}, format="json", **key())
        ids = r.json()["created_line_ids"]
        r = self.c.post(f"/api/visits/{v['id']}/lines/cancel-batch", {"line_ids": ids, "reason": "入力ミス（注文直後の取り消し）"}, format="json", **key())
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(Decimal(r.json()["quote"]["total"]), Decimal(5000))
        self.assertEqual(self.stock(self.p["champagne"]), Decimal(6))
        # 有効なバック（確定・未確定）の合計が 0 になる（未確定分は取消状態、確定分は打消しで相殺）
        net = sum((b.amount for b in BackEntry.objects.filter(employee=self.cast1, status__in=["pending", "confirmed"])), Decimal(0))
        self.assertEqual(net, Decimal(0))
        self.assertTrue(BackEntry.objects.filter(employee=self.cast1, status="reversed").exists())
        self.assertTrue(all(l.cancel_reason for l in OrderLine.objects.filter(id__in=ids)))

    def test_cancel_batch_requires_reason(self):
        v = self.open_visit()
        r = self.c.post(f"/api/visits/{v['id']}/lines/batch", {"items": [{"kind": "product", "product_id": self.p["beer"].id}]}, format="json", **key())
        ids = r.json()["created_line_ids"]
        r = self.c.post(f"/api/visits/{v['id']}/lines/cancel-batch", {"line_ids": ids, "reason": ""}, format="json", **key())
        self.assertEqual(r.status_code, 400)
        self.assertEqual(OrderLine.objects.get(id=ids[0]).status, "active")


class ExtendPreviewAndUndoTests(PosBase):
    def test_preview_matches_extension_and_undo_restores_end(self):
        v = self.open_visit()
        entry = v["course_entries"][0]
        pv = self.c.get(f"/api/visits/{v['id']}/course-entries/{entry['id']}/extend-preview").json()
        self.assertEqual(Decimal(pv["amount"]), Decimal(5000))  # 2,500 × 2名
        r = self.c.post(f"/api/visits/{v['id']}/course-entries/{entry['id']}/extend", {}, format="json", **key())
        data = r.json()
        e2 = [e for e in data["course_entries"] if e["id"] == entry["id"]][0]
        from datetime import datetime

        self.assertEqual(datetime.fromisoformat(e2["planned_end_at"]), datetime.fromisoformat(pv["to_planned_end_at"]))
        # 直後の取り消しで終了予定と金額が元に戻る
        r = self.c.post(f"/api/visits/{v['id']}/lines/cancel-batch", {"line_ids": [data["created_line_id"]], "reason": "入力ミス（注文直後の取り消し）"}, format="json", **key())
        self.assertEqual(r.status_code, 200, r.content)
        e3 = [e for e in r.json()["course_entries"] if e["id"] == entry["id"]][0]
        self.assertEqual(e3["planned_end_at"], entry["planned_end_at"])
        self.assertEqual(e3["extension_count"], 0)
        self.assertEqual(Decimal(r.json()["quote"]["total"]), Decimal(5000))


class CloseBlockedByOpenVisitTests(PosBase):
    def test_cannot_close_with_open_visit_then_can_after_checkout(self):
        self.c.post("/api/cash/open", {"opening_float": "30000"}, format="json", **key())
        v = self.open_visit()
        cur = self.c.get("/api/cash/current").json()["day"]
        self.assertEqual([x["visit_id"] for x in cur["open_visits"]], [v["id"]])
        r = self.c.post("/api/cash/close", {"counted_cash": "30000", "next_day_float": "30000"}, format="json", **key())
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["error"]["code"], "open_visits")
        self.assertIn("1卓", r.json()["error"]["message"])
        # 会計すれば締められる
        services.confirm_checkout(self.store, self.fx.users["manager"], v["id"], {"payments": [{"method": "cash", "amount": "5000", "tendered": "10000"}]})
        r = self.c.post("/api/cash/close", {"counted_cash": "35000", "next_day_float": "30000"}, format="json", **key())
        self.assertEqual(r.status_code, 200, r.content)

    def test_cancelled_visit_does_not_block(self):
        self.c.post("/api/cash/open", {"opening_float": "30000"}, format="json", **key())
        v = self.open_visit()
        services.cancel_visit(self.store, self.fx.users["manager"], v["id"], "誤登録")
        r = self.c.post("/api/cash/close", {"counted_cash": "30000", "next_day_float": "30000"}, format="json", **key())
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(Visit.objects.get(id=v["id"]).status, "cancelled")
