"""領収書を渡した記録（売上・会計履歴で発行済みかどうかを確かめる）。"""
from __future__ import annotations

from decimal import Decimal

from apps.core import void_pin
from apps.core.models import AuditLog
from apps.core.testing import client_for, make_store
from apps.sales import services
from apps.sales.models import ReceiptIssue

from .test_flow import FlowBase, key


class ReceiptIssueTests(FlowBase):
    def _confirmed(self, payments=None):
        v = self.open_visit()
        self.add_line(v["id"], {"kind": "product", "product_id": self.p["beer"].id, "quantity": 1})
        # 2500（セット）+ 800（ビール）= 3300
        return self.checkout(v["id"], payments or [{"method": "cash", "amount": "2000", "tendered": "2000"}, {"method": "card", "amount": "1300"}])

    def issue(self, co, body=None, client=None, k=None):
        return (client or self.c).post(f"/api/checkouts/{co['id']}/receipt-issues", body or {}, format="json", **(k or key()))

    def history_row(self, co):
        rows = self.c.get("/api/sales/history").json()["checkouts"]
        return next(r for r in rows if r["id"] == co["id"])

    def test_record_and_show_in_history(self):
        co = self._confirmed()
        row = self.history_row(co)
        self.assertEqual(Decimal(row["receipt_amount"]), Decimal("3300"))
        self.assertEqual(row["receipt_issues"], [])

        r = self.issue(co, {"addressee": "  株式会社テスト  ", "proviso": "お飲食代として"})
        self.assertEqual(r.status_code, 201, r.content)
        d = r.json()
        self.assertEqual((d["method"], d["method_label"], Decimal(d["amount"]), d["addressee"], d["proviso"], d["reissue"]),
                         ("handwritten", "手書き", Decimal("3300"), "株式会社テスト", "お飲食代として", False))
        self.assertEqual(d["issued_by_name"], str(self.fx.users["manager"]))

        row = self.history_row(co)
        self.assertEqual(len(row["receipt_issues"]), 1)
        self.assertEqual(row["receipt_issues"][0]["id"], d["id"])
        # 手書き領収書の転記画面にも出る
        hw = self.c.get(f"/api/checkouts/{co['id']}/handwrite").json()
        self.assertEqual(Decimal(hw["receipt_amount"]), Decimal("3300"))
        self.assertEqual([x["id"] for x in hw["receipt_issues"]], [d["id"]])
        # 操作履歴にも残る
        log = AuditLog.objects.get(action="receipt.issue")
        self.assertEqual((log.target_type, log.target_id), ("Checkout", str(co["id"])))
        self.assertEqual(log.changes["addressee"], "株式会社テスト")

    def test_second_issue_is_reissue(self):
        co = self._confirmed()
        self.assertFalse(self.issue(co).json()["reissue"])
        self.assertTrue(self.issue(co).json()["reissue"])
        self.assertEqual([x["reissue"] for x in self.history_row(co)["receipt_issues"]], [False, True])

    def test_receivable_is_not_in_receipt_amount(self):
        co = self._confirmed([{"method": "cash", "amount": "1000", "tendered": "1000"}, {"method": "receivable", "amount": "2300", "debtor_name": "山田"}])
        self.assertEqual(Decimal(self.history_row(co)["receipt_amount"]), Decimal("1000"))
        self.assertEqual(Decimal(self.issue(co).json()["amount"]), Decimal("1000"))

    def test_all_receivable_cannot_issue(self):
        co = self._confirmed([{"method": "receivable", "amount": "3300", "debtor_name": "山田"}])
        self.assertEqual(Decimal(self.history_row(co)["receipt_amount"]), Decimal("0"))
        r = self.issue(co)
        self.assertEqual(r.status_code, 400)
        self.assertFalse(ReceiptIssue.objects.exists())

    def test_voided_checkout_cannot_issue(self):
        co = self._confirmed()
        services.void_checkout(self.store, self.fx.users["manager"], co["id"], "テスト")
        r = self.issue(co)
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["error"]["code"], "checkout_voided")

    def test_replay_does_not_record_twice(self):
        co = self._confirmed()
        k = key()
        r1 = self.issue(co, k=k)
        r2 = self.issue(co, k=k)
        self.assertEqual((r1.status_code, r2.status_code), (201, 201))
        self.assertEqual(r2["Idempotent-Replayed"], "true")
        self.assertEqual(ReceiptIssue.objects.count(), 1)

    def test_other_store_ids_are_rejected(self):
        co = self._confirmed()
        other = make_store("s2", company_code="c2")
        ov = services.open_visit(other.store, self.fx.users["manager"], {"table_id": other.tables[0].id, "entries": [{"guest_count": 1, "course_id": other.hourly.id}]})
        oco = services.confirm_checkout(other.store, self.fx.users["manager"], ov.id, {"payments": [{"method": "cash", "amount": "2500", "tendered": "2500"}]})
        # 別店舗の会計 ID
        self.assertEqual(self.c.post(f"/api/checkouts/{oco.id}/receipt-issues", {}, format="json", **key()).status_code, 404)
        self.assertFalse(ReceiptIssue.objects.filter(checkout_id=oco.id).exists())
        # 別店舗の記録 ID は取り消せない
        oissue = ReceiptIssue.objects.create(store=other.store, checkout=oco, amount=Decimal("2500"), issued_at=oco.confirmed_at)
        self.assertEqual(self.c.post(f"/api/receipt-issues/{oissue.id}/cancel", {}, format="json", **key()).status_code, 404)
        oissue.refresh_from_db()
        self.assertIsNone(oissue.cancelled_at)
        # 自店舗の一覧に別店舗の記録は混ざらない
        self.issue(co)
        self.assertEqual(len(self.history_row(co)["receipt_issues"]), 1)

    def test_permissions(self):
        co = self._confirmed()
        staff = client_for(self.fx.users["staff"], self.store)
        cast = client_for(self.fx.users["cast"], self.store)
        # 記録はホールの操作ができる人（スタッフ）まで
        r = self.issue(co, client=staff)
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(self.issue(co, client=cast).status_code, 403)
        # 記録の取消は会計の取消と同じ権限（スタッフは不可）
        iid = r.json()["id"]
        self.assertEqual(staff.post(f"/api/receipt-issues/{iid}/cancel", {}, format="json", **key()).status_code, 403)

    def test_cancel_record(self):
        co = self._confirmed()
        first = self.issue(co).json()
        r = self.c.post(f"/api/receipt-issues/{first['id']}/cancel", {}, format="json", **key())
        self.assertEqual(r.status_code, 200, r.content)
        self.assertIsNotNone(r.json()["cancelled_at"])
        self.assertEqual(r.json()["cancelled_by_name"], str(self.fx.users["manager"]))
        # 取り消した記録も一覧に残る（cancelled_at 付き）。数えるのは取り消していないものだけなので、次は再発行にならない
        issues = self.history_row(co)["receipt_issues"]
        self.assertEqual(len(issues), 1)
        self.assertIsNotNone(issues[0]["cancelled_at"])
        self.assertFalse(self.issue(co).json()["reissue"])
        # 二度目の取消はできない
        r = self.c.post(f"/api/receipt-issues/{first['id']}/cancel", {}, format="json", **key())
        self.assertEqual(r.status_code, 409)
        self.assertTrue(AuditLog.objects.filter(action="receipt.issue.cancel").exists())

    def test_checkout_delete_still_works_after_issue(self):
        void_pin.set_pin(self.store, "4826")
        co = self._confirmed()
        self.issue(co)
        r = self.c.post(f"/api/checkouts/{co['id']}/delete", {"pin": "4826", "mode": "redo"}, format="json", **key())
        self.assertEqual(r.status_code, 200, r.content)
        self.assertFalse(ReceiptIssue.objects.filter(checkout_id=co["id"]).exists())
        # 渡した記録は操作履歴に残っている
        self.assertTrue(AuditLog.objects.filter(action="receipt.issue", target_id=str(co["id"])).exists())

    def test_long_text_is_cut(self):
        co = self._confirmed()
        d = self.issue(co, {"addressee": "あ" * 100, "proviso": "い" * 100}).json()
        self.assertEqual((len(d["addressee"]), len(d["proviso"])), (60, 40))
