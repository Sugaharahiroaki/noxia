"""会計の取消（記録ごと削除・理由なし・店舗の 4 桁の暗証番号）。"""
from __future__ import annotations

from decimal import Decimal

from apps.cash.models import BusinessDay
from apps.core import void_pin
from apps.core.models import AuditLog, StoreSettings
from apps.core.testing import client_for
from apps.sales.models import Checkout, OrderLine, Payment, Receivable, Visit
from apps.staff.models import BackEntry

from .test_flow import FlowBase, key


class CheckoutDeleteTests(FlowBase):
    def setUp(self):
        super().setUp()
        void_pin.set_pin(self.store, "4826")

    def _confirmed(self, payments=None):
        v = self.open_visit()
        self.add_line(v["id"], {"kind": "product", "product_id": self.p["cast_drink"].id, "quantity": 2, "back_targets": [{"employee_id": self.cast1.id}]})
        co = self.checkout(v["id"], payments or [{"method": "cash", "amount": "3500", "tendered": "4000"}, {"method": "card", "amount": "2000"}])
        return v, co

    def delete(self, co, pin="4826", client=None):
        return (client or self.c).post(f"/api/checkouts/{co['id']}/delete", {"pin": pin}, format="json", **key())

    def test_delete_removes_records_and_returns_slip(self):
        v, co = self._confirmed()
        before_cash = Decimal(self.c.get("/api/cash/current").json()["day"]["summary"]["theoretical_cash"])
        self.assertEqual(BackEntry.objects.filter(checkout_id=co["id"], status="confirmed").count(), 1)
        r = self.delete(co)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["deleted"]["number"], co["number"])
        # 会計・支払いは記録ごと消える
        self.assertFalse(Checkout.objects.filter(id=co["id"]).exists())
        self.assertFalse(Payment.objects.filter(checkout_id=co["id"]).exists())
        # 伝票は会計前に戻り、明細はそのまま
        visit = Visit.objects.get(id=v["id"])
        self.assertEqual(visit.status, "open")
        self.assertEqual(OrderLine.objects.filter(visit=visit, checkout__isnull=True, status="active").count(), 2)
        self.assertEqual(self.quote(v["id"]), Decimal("5500"))
        # バックは打消しを作らず会計前に戻る（会計で作ったものは消える）
        self.assertFalse(BackEntry.objects.filter(reversal_of__isnull=False).exists())
        self.assertEqual(sum(e.amount for e in BackEntry.objects.filter(employee=self.cast1, status="confirmed")), Decimal("0"))
        # 売上・レジからも消える（取消件数にも出ない）
        hist = self.c.get("/api/sales/history").json()["summary"]
        self.assertEqual((hist["checkout_count"], hist["voided_count"], Decimal(hist["total"])), (0, 0, Decimal("0")))
        after_cash = Decimal(self.c.get("/api/cash/current").json()["day"]["summary"]["theoretical_cash"])
        self.assertEqual(after_cash, before_cash - Decimal("3500"))
        # 操作履歴にだけ残る
        log = AuditLog.objects.filter(action="checkout.delete").latest("id")
        self.assertEqual(log.changes["total"], co["total"])
        # 二度目は見つからない
        self.assertEqual(self.delete(co).status_code, 404)
        # 戻った明細で会計し直せる（バックも作り直される）
        co2 = self.checkout(v["id"], [{"method": "cash", "amount": "5500", "tendered": "5500"}])
        self.assertEqual(Decimal(co2["total"]), Decimal("5500"))
        self.assertEqual(BackEntry.objects.filter(checkout_id=co2["id"], status="confirmed").count(), 1)
        self.assertEqual(sum(e.amount for e in BackEntry.objects.filter(employee=self.cast1, status="confirmed")), Decimal("1500"))

    def test_receivable_is_removed(self):
        StoreSettings.objects.filter(store=self.store).update(receivable_allowed=True)
        v = self.open_visit()
        co = self.checkout(v["id"], [{"method": "receivable", "amount": "2500", "debtor_name": "山田"}])
        self.assertEqual(Receivable.objects.filter(checkout_id=co["id"]).count(), 1)
        self.assertEqual(self.delete(co).status_code, 200)
        self.assertFalse(Receivable.objects.filter(checkout_id=co["id"]).exists())

    def test_wrong_pin_and_lockout(self):
        _, co = self._confirmed()
        for remaining in (4, 3, 2, 1):
            r = self.delete(co, pin="0001")
            self.assertEqual(r.status_code, 403)
            self.assertEqual(r.json()["error"]["code"], "void_pin_wrong")
            self.assertEqual(r.json()["error"]["details"]["remaining"], remaining)
        r = self.delete(co, pin="0001")
        self.assertEqual(r.status_code, 423)
        # ロック中は正しい番号でも止まる
        r = self.delete(co)
        self.assertEqual(r.status_code, 423)
        self.assertEqual(r.json()["error"]["code"], "void_pin_locked")
        self.assertTrue(Checkout.objects.filter(id=co["id"]).exists())
        # 時間が過ぎれば使える
        StoreSettings.objects.filter(store=self.store).update(void_pin_locked_until=None)
        self.assertEqual(self.delete(co).status_code, 200)

    def test_pin_not_set(self):
        void_pin.set_pin(self.store, None)
        _, co = self._confirmed()
        r = self.delete(co, pin="")
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["error"]["code"], "void_pin_not_set")

    def test_closed_day_and_refund_block(self):
        _, co = self._confirmed()
        r = self.c.post(f"/api/checkouts/{co['id']}/refunds", {"amount": "500", "method": "cash", "reason": "x"}, format="json", **key())
        self.assertEqual(r.status_code, 201, r.content)
        r = self.delete(co)
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["error"]["code"], "has_refunds")
        _, co2 = self._confirmed()
        BusinessDay.objects.filter(store=self.store).update(status="closed")
        r = self.delete(co2)
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["error"]["code"], "day_closed")

    def test_staff_cannot_delete_and_pin_is_needed_for_old_void(self):
        _, co = self._confirmed()
        staff = client_for(self.fx.users["staff"], self.store)
        self.assertEqual(self.delete(co, client=staff).status_code, 403)
        # 暗証番号を設定した店舗では、旧来の取消 API も番号なしでは通らない
        r = self.c.post(f"/api/checkouts/{co['id']}/void", {"reason": "x"}, format="json", **key())
        self.assertEqual(r.status_code, 403)

    def test_pin_setting_owner_only_and_hidden(self):
        owner = client_for(self.fx.users["owner"], self.store)
        r = owner.put("/api/store/void-pin", {"pin": "12a4"}, format="json")
        self.assertEqual(r.status_code, 400)
        r = owner.put("/api/store/void-pin", {"pin": "1234"}, format="json")
        self.assertEqual(r.status_code, 400)  # 推測されやすい番号
        r = owner.put("/api/store/void-pin", {"pin": "7391"}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        # 店長（店舗タブレットの共有アカウント）は変えられない
        r = self.c.put("/api/store/void-pin", {"pin": "5555"}, format="json")
        self.assertEqual(r.status_code, 403)
        s = owner.get("/api/store/settings").json()
        self.assertTrue(s["void_pin_set"])
        self.assertNotIn("void_pin_hash", s)
        self.assertNotIn("7391", str(s))
        _, co = self._confirmed()
        self.assertEqual(self.delete(co, pin="4826").status_code, 403)  # 古い番号は使えない
        self.assertEqual(self.delete(co, pin="7391").status_code, 200)

    def test_full_cancel_returns_all_money_and_keeps_backs_in_pay(self):
        from apps.core.business_date import business_date_now
        from apps.staff import payroll

        base_cash = Decimal(self.c.get("/api/cash/current").json()["day"]["summary"]["theoretical_cash"])
        v, co = self._confirmed()
        r = self.c.post(f"/api/checkouts/{co['id']}/delete", {"pin": "4826", "mode": "refund"}, format="json", **key())
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(Decimal(r.json()["deleted"]["kept_backs"]), Decimal("1500"))
        # 会計・支払いは消え、伝票は取消、明細も取消
        self.assertFalse(Checkout.objects.filter(id=co["id"]).exists())
        self.assertFalse(Payment.objects.filter(checkout_id=co["id"]).exists())
        self.assertEqual(Visit.objects.get(id=v["id"]).status, "cancelled")
        self.assertFalse(OrderLine.objects.filter(visit_id=v["id"], status="active").exists())
        # 売上ゼロ、レジは会計前と同じ（全額返した）
        hist = self.c.get("/api/sales/history").json()["summary"]
        self.assertEqual((hist["checkout_count"], Decimal(hist["total"])), (0, Decimal("0")))
        self.assertEqual(Decimal(self.c.get("/api/cash/current").json()["day"]["summary"]["theoretical_cash"]), base_cash)
        # 女の子のバックは確定のまま給与に入る（打消しなし）
        self.assertFalse(BackEntry.objects.filter(reversal_of__isnull=False).exists())
        bd = business_date_now(self.store)
        self.assertEqual(Decimal(payroll.compute_statement(self.store, self.cast1, bd, bd)["back_amount"]), Decimal("1500"))
