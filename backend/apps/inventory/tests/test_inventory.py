"""在庫台帳・棚卸し・発注候補・発注ライフサイクル・ボトルキープの在庫整合性。"""
from __future__ import annotations

import uuid
from decimal import Decimal

from django.utils import timezone
from rest_framework.test import APITestCase

from apps.core.testing import client_for, standard_fixture
from apps.customers.models import BottleKeep, Customer
from apps.inventory.models import PurchaseOrder, StockMovement, Supplier, SupplierProduct
from apps.inventory.services import current_stock
from apps.sales import services


def key():
    return {"HTTP_IDEMPOTENCY_KEY": str(uuid.uuid4())}


class InventoryBase(APITestCase):
    def setUp(self):
        self.fx = standard_fixture()
        self.store = self.fx.store
        self.user = self.fx.users["manager"]
        self.manager = client_for(self.user, self.store)
        self.staff = client_for(self.fx.users["staff"], self.store)
        self.owner = client_for(self.fx.users["owner"], self.store)
        self.p = self.fx.products
        now = timezone.now()
        StockMovement.objects.create(store=self.store, product=self.p["champagne"], kind="receive", quantity=Decimal(6), occurred_at=now)
        StockMovement.objects.create(store=self.store, product=self.p["tequila"], kind="receive", quantity=Decimal(2), occurred_at=now)

    def visit(self, customer=None):
        data = {"table_id": self.fx.tables[0].id, "entries": [{"guest_count": 1, "course_id": self.fx.hourly.id}]}
        if customer:
            data["customer_id"] = customer.id
        return services.open_visit(self.store, self.user, data)


class StockLedgerTests(InventoryBase):
    def test_bottle_sale_and_cancel(self):
        v = self.visit()
        line = services.add_product_line(self.store, self.user, v.id, {"product_id": self.p["champagne"].id, "quantity": 2})
        self.assertEqual(current_stock(self.p["champagne"]), Decimal("4"))
        mv = StockMovement.objects.get(order_line=line, kind="sale")
        self.assertEqual(mv.quantity, Decimal("-2"))
        self.assertFalse(mv.is_theoretical)
        services.cancel_line(self.store, self.user, v.id, line.id, "誤注文")
        self.assertEqual(current_stock(self.p["champagne"]), Decimal("6"))
        self.assertEqual(StockMovement.objects.filter(order_line=line, kind="sale_reversal").get().quantity, Decimal("2"))

    def test_shot_sale_is_theoretical(self):
        v = self.visit()
        line = services.add_product_line(self.store, self.user, v.id, {"product_id": self.p["tequila"].id, "quantity": 3})
        mv = StockMovement.objects.get(order_line=line, kind="sale")
        self.assertTrue(mv.is_theoretical)
        self.assertEqual(mv.quantity, Decimal("-0.1200"))  # 3 × 30ml / 750ml
        self.assertEqual(current_stock(self.p["tequila"]), Decimal("1.8800"))
        rows = {r["product_id"]: r for r in self.manager.get("/api/inventory/stock").json()}
        t = rows[self.p["tequila"].id]
        self.assertTrue(t["is_theoretical"])
        self.assertEqual(t["quantity_bottles"], "1.88")
        self.assertEqual(t["shots_remaining_est"], "47")
        self.assertFalse(rows[self.p["champagne"].id]["is_theoretical"])
        self.assertNotIn(self.p["beer"].id, rows)  # 在庫管理しない商品は出ない

    def test_no_stock_mode_product_creates_no_movement(self):
        v = self.visit()
        line = services.add_product_line(self.store, self.user, v.id, {"product_id": self.p["beer"].id, "quantity": 5})
        self.assertFalse(StockMovement.objects.filter(order_line=line).exists())

    def test_manual_movements_api(self):
        r = self.staff.post("/api/inventory/movements", {"product_id": self.p["champagne"].id, "kind": "waste", "quantity": "1", "reason": "破損"}, format="json", **key())
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(Decimal(r.json()["quantity"]), Decimal("-1"))
        self.assertEqual(current_stock(self.p["champagne"]), Decimal("5"))
        r = self.staff.post("/api/inventory/movements", {"product_id": self.p["champagne"].id, "kind": "waste", "quantity": "1"}, format="json", **key())
        self.assertEqual(r.status_code, 400)  # 理由必須
        r = self.staff.post("/api/inventory/movements", {"product_id": self.p["champagne"].id, "kind": "receive", "quantity": "3"}, format="json", **key())
        self.assertEqual(r.status_code, 201)
        self.assertEqual(current_stock(self.p["champagne"]), Decimal("8"))
        r = self.staff.post("/api/inventory/movements", {"product_id": self.p["champagne"].id, "kind": "sale", "quantity": "1", "reason": "x"}, format="json", **key())
        self.assertEqual(r.status_code, 400)
        self.assertEqual(len(self.staff.get(f"/api/inventory/movements?product={self.p['champagne'].id}").json()), 3)

    def test_stock_take_applies_adjustment(self):
        v = self.visit()
        services.add_product_line(self.store, self.user, v.id, {"product_id": self.p["tequila"].id, "quantity": 5})  # 2 − 0.2 = 1.8
        r = self.staff.post("/api/inventory/stock-takes", {"lines": [
            {"product_id": self.p["champagne"].id, "counted_full_bottles": "4"},
            {"product_id": self.p["tequila"].id, "counted_full_bottles": "1", "counted_open_ml": "300"},
        ], "apply": True, "note": "月末棚卸し"}, format="json", **key())
        self.assertEqual(r.status_code, 201, r.content)
        d = r.json()
        self.assertEqual(d["status"], "applied")
        lines = {l["product"]: l for l in d["lines"]}
        ch = lines[self.p["champagne"].id]
        self.assertEqual(Decimal(ch["theoretical_qty"]), Decimal("6"))
        self.assertEqual(Decimal(ch["counted_qty"]), Decimal("4"))
        self.assertEqual(Decimal(ch["difference"]), Decimal("-2"))
        tq = lines[self.p["tequila"].id]
        self.assertEqual(Decimal(tq["theoretical_qty"]), Decimal("1.8"))
        self.assertEqual(Decimal(tq["counted_qty"]), Decimal("1.4"))  # 1本 + 300/750
        self.assertEqual(Decimal(tq["difference"]), Decimal("-0.4"))
        self.assertEqual(current_stock(self.p["champagne"]), Decimal("4"))
        self.assertEqual(current_stock(self.p["tequila"]), Decimal("1.4"))
        self.assertEqual(StockMovement.objects.filter(kind="adjust").count(), 2)
        # apply=false は記録のみ
        r = self.staff.post("/api/inventory/stock-takes", {"lines": [{"product_id": self.p["champagne"].id, "counted_full_bottles": "3"}], "apply": False}, format="json", **key())
        self.assertEqual(r.json()["status"], "draft")
        self.assertEqual(current_stock(self.p["champagne"]), Decimal("4"))
        r = self.staff.post("/api/inventory/stock-takes", {"lines": [{"product_id": self.p["beer"].id, "counted_full_bottles": "3"}], "apply": True}, format="json", **key())
        self.assertEqual(r.status_code, 400)

    def test_reorder_candidates(self):
        supplier = Supplier.objects.create(store=self.store, name="酒販")
        sp = SupplierProduct.objects.create(store=self.store, supplier=supplier, product=self.p["champagne"], purchase_price=Decimal(54000), order_unit_name="ケース", units_per_order_unit=Decimal(6), is_preferred=True)
        self.assertEqual(self.staff.get("/api/inventory/reorder-candidates").json(), [])  # 6本 > 発注点2
        v = self.visit()
        services.add_product_line(self.store, self.user, v.id, {"product_id": self.p["champagne"].id, "quantity": 5})
        rows = self.staff.get("/api/inventory/reorder-candidates").json()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["product_id"], self.p["champagne"].id)
        self.assertEqual(row["quantity_bottles"], "1.00")
        self.assertEqual(row["suggested_bottles"], "3")  # min_stock 4 − 1
        self.assertEqual(row["supplier_product_id"], sp.id)
        self.assertEqual(row["order_unit_name"], "ケース")
        self.assertEqual(row["units_per_order_unit"], "6.00")


class PurchaseOrderTests(InventoryBase):
    def setUp(self):
        super().setUp()
        self.supplier = Supplier.objects.create(store=self.store, name="酒販X", order_method="電話")
        self.sp = SupplierProduct.objects.create(store=self.store, supplier=self.supplier, product=self.p["champagne"], purchase_price=Decimal(54000), order_unit_name="ケース", units_per_order_unit=Decimal(6), is_preferred=True)

    def _draft(self, client=None):
        client = client or self.staff
        r = client.post("/api/inventory/purchase-orders", {"supplier_id": self.supplier.id, "lines": [{"product_id": self.p["champagne"].id, "order_units": "1"}], "note": "急ぎ"}, format="json", **key())
        self.assertEqual(r.status_code, 201, r.content)
        return r.json()

    def test_lifecycle_with_partial_receipt(self):
        po = self._draft()
        self.assertEqual(po["status"], "draft")
        self.assertEqual(po["number"], 1)
        line = po["lines"][0]
        self.assertEqual(line["expected_bottles"], "6.00")
        self.assertEqual(Decimal(line["unit_price"]), Decimal("54000"))
        self.assertEqual(Decimal(po["total_cost"]), Decimal("54000"))
        pid = po["id"]
        # 確認はオーナーのみ
        self.assertEqual(self.staff.post(f"/api/inventory/purchase-orders/{pid}/confirm", {}, format="json", **key()).status_code, 403)
        self.assertEqual(self.manager.post(f"/api/inventory/purchase-orders/{pid}/confirm", {}, format="json", **key()).status_code, 403)
        # 確認前に発注済みにはできない（自動送信もしない）
        self.assertEqual(self.staff.post(f"/api/inventory/purchase-orders/{pid}/mark-ordered", {"ordered_via": "電話"}, format="json", **key()).status_code, 409)
        self.assertEqual(self.staff.post(f"/api/inventory/purchase-orders/{pid}/receive", {"lines": [{"po_line_id": line["id"], "bottles": "1"}]}, format="json", **key()).status_code, 409)
        r = self.owner.post(f"/api/inventory/purchase-orders/{pid}/confirm", {}, format="json", **key())
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["status"], "confirmed")
        self.assertIsNotNone(r.json()["confirmed_at"])
        # 確認後は下書き編集不可
        self.assertEqual(self.staff.patch(f"/api/inventory/purchase-orders/{pid}", {"note": "x"}, format="json").status_code, 409)
        r = self.staff.post(f"/api/inventory/purchase-orders/{pid}/mark-ordered", {}, format="json", **key())
        self.assertEqual(r.status_code, 400)  # 連絡手段必須
        r = self.staff.post(f"/api/inventory/purchase-orders/{pid}/mark-ordered", {"ordered_via": "電話"}, format="json", **key())
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["status"], "ordered")
        self.assertEqual(r.json()["ordered_via"], "電話")
        before = current_stock(self.p["champagne"])
        r = self.staff.post(f"/api/inventory/purchase-orders/{pid}/receive", {"lines": [{"po_line_id": line["id"], "bottles": "2"}]}, format="json", **key())
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()["status"], "partially_received")
        self.assertEqual(Decimal(r.json()["lines"][0]["received_bottles"]), Decimal("2"))
        self.assertEqual(current_stock(self.p["champagne"]), before + 2)
        r = self.staff.post(f"/api/inventory/purchase-orders/{pid}/receive", {"lines": [{"po_line_id": line["id"], "bottles": "4"}]}, format="json", **key())
        self.assertEqual(r.json()["status"], "received")
        self.assertEqual(current_stock(self.p["champagne"]), before + 6)
        self.assertEqual(len(r.json()["receipts"]), 2)
        self.assertEqual(StockMovement.objects.filter(kind="receive", reference="発注#1").count(), 2)
        # 入荷済みは取消不可
        self.assertEqual(self.staff.post(f"/api/inventory/purchase-orders/{pid}/cancel", {}, format="json", **key()).status_code, 409)

    def test_export_text_and_csv(self):
        po = self._draft()
        r = self.staff.get(f"/api/inventory/purchase-orders/{po['id']}/export")
        self.assertEqual(r.status_code, 200)
        self.assertIn("シャンパン × 1ケース（6本）", r.json()["text"])
        self.assertIn("酒販X", r.json()["text"])
        self.assertEqual(self.staff.get(f"/api/inventory/purchase-orders/{po['id']}/export?format=csv").status_code, 403)
        r = self.manager.get(f"/api/inventory/purchase-orders/{po['id']}/export?format=csv")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r["Content-Type"].startswith("text/csv"))
        self.assertIn("シャンパン", r.content.decode("utf-8-sig"))

    def test_draft_validation_and_cancel(self):
        r = self.staff.post("/api/inventory/purchase-orders", {"supplier_id": self.supplier.id, "lines": []}, format="json", **key())
        self.assertEqual(r.status_code, 400)
        r = self.staff.post("/api/inventory/purchase-orders", {"supplier_id": self.supplier.id, "lines": [{"product_id": self.p["champagne"].id, "order_units": "0"}]}, format="json", **key())
        self.assertEqual(r.status_code, 400)
        other = standard_fixture("z1", "cz")
        r = self.staff.post("/api/inventory/purchase-orders", {"supplier_id": self.supplier.id, "lines": [{"product_id": other.products["champagne"].id, "order_units": "1"}]}, format="json", **key())
        self.assertEqual(r.status_code, 400)
        po = self._draft()
        r = self.staff.patch(f"/api/inventory/purchase-orders/{po['id']}", {"lines": [{"product_id": self.p["champagne"].id, "order_units": "2"}], "note": "変更"}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(Decimal(r.json()["lines"][0]["order_units"]), Decimal("2"))
        r = self.staff.post(f"/api/inventory/purchase-orders/{po['id']}/cancel", {}, format="json", **key())
        self.assertEqual(r.json()["status"], "cancelled")
        self.assertEqual(PurchaseOrder.objects.get(id=po["id"]).status, "cancelled")
        # 仕入先マスタの変更はスタッフ不可
        self.assertEqual(self.staff.post("/api/inventory/suppliers", {"name": "x"}, format="json").status_code, 403)
        self.assertEqual(self.manager.post("/api/inventory/suppliers", {"name": "x"}, format="json").status_code, 201)


class BottleKeepTests(InventoryBase):
    def test_keep_from_sale_does_not_reduce_stock_twice(self):
        customer = Customer.objects.create(company=self.fx.company, store=self.store, display_name="田中様")
        v = self.visit(customer)
        line = services.add_product_line(self.store, self.user, v.id, {"product_id": self.p["champagne"].id, "quantity": 1, "register_bottle_keep": True, "keep_location": "棚A"})
        self.assertEqual(current_stock(self.p["champagne"]), Decimal("5"))
        self.assertEqual(StockMovement.objects.filter(product=self.p["champagne"]).exclude(kind="receive").count(), 1)
        keep = BottleKeep.objects.get(source_order_line=line)
        self.assertEqual(keep.customer_id, customer.id)
        self.assertEqual(keep.location, "棚A")
        self.assertEqual(keep.status, "active")
        self.assertEqual(keep.name, "シャンパン")
        # キープ対象外・顧客なしでは作られない
        v2 = self.visit()
        services.add_product_line(self.store, self.user, v2.id, {"product_id": self.p["champagne"].id, "quantity": 1, "register_bottle_keep": True})
        self.assertEqual(BottleKeep.objects.count(), 1)
        # 手動登録のキープも在庫を動かさない
        r = self.manager.post("/api/bottle-keeps", {"customer": customer.id, "product": self.p["champagne"].id, "name": "持ち込み", "registered_on": "2026-01-01"}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(current_stock(self.p["champagne"]), Decimal("4"))
        hist = self.manager.get(f"/api/customers/{customer.id}/history").json()
        self.assertEqual(len(hist["bottles"]), 2)
        self.assertEqual(len(hist["visits"]), 1)
