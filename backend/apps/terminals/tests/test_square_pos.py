"""Square リーダー（iPad の Bluetooth・Square のアプリ経由）: Square のアプリを開く URL、戻ってきた結果での会計、照合、別店舗。"""
from __future__ import annotations

import json
import urllib.parse
from decimal import Decimal
from unittest import mock

from rest_framework.test import APIClient

from apps.core.testing import client_for, make_store
from apps.sales.models import Checkout, Payment
from apps.terminals.models import CardTerminal, TerminalConfig, TerminalPayment

from .test_terminals import FakeResponse, TerminalBase, key

CALLBACK = "https://noxia.example.jp/square-callback"


def handoff_data(url: str) -> dict:
    assert url.startswith("square-commerce-v1://payment/create?data="), url
    return json.loads(urllib.parse.unquote(url.split("?data=", 1)[1]))


class SquarePosTests(TerminalBase):
    def setUp(self):
        super().setUp()
        TerminalConfig.objects.filter(store=self.store).update(
            provider="square_pos", square_application_id="sq0idp-TEST", square_pos_callback_url=CALLBACK, square_access_token="", square_location_id="",
        )
        CardTerminal.objects.filter(store=self.store).delete()
        r = self.c.post("/api/terminals/devices", {"name": "iPad の Square リーダー"}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()["status"], "paired")  # Bluetooth の接続は Square のアプリ側。NOXIA でのペアリングは無い
        self.terminal = CardTerminal.objects.get(id=r.json()["id"])
        self.public = APIClient()  # Square のアプリから戻ってきた Safari（ログインしていないこともある）

    def callback(self, **data):
        return self.public.post("/api/terminals/square-pos/callback", {"data": json.dumps(data)}, format="json")

    def start(self, **body):
        v = self.open_visit()
        r = self.send(v["id"], **body)
        self.assertEqual(r.status_code, 201, r.content)
        tp = r.json()
        return v, tp, handoff_data(tp["handoff_url"])

    def test_status_and_handoff_url(self):
        st = self.c.get("/api/terminals/status").json()
        self.assertEqual(st["mode"], "square_pos")
        self.assertTrue(st["enabled"])
        _, tp, d = self.start()
        self.assertEqual(d["amount_money"], {"amount": "5800", "currency_code": "JPY"})
        self.assertEqual(d["client_id"], "sq0idp-TEST")
        self.assertEqual(d["callback_url"], CALLBACK)
        self.assertEqual(d["version"], "1.3")
        self.assertEqual(d["options"]["supported_tender_types"], ["CREDIT_CARD"])
        self.assertTrue(d["options"]["auto_return"])
        self.assertGreaterEqual(len(d["state"]), 30)
        self.assertEqual(TerminalPayment.objects.get(id=tp["id"]).callback_token, d["state"])

    def test_incomplete_config_is_not_usable(self):
        TerminalConfig.objects.filter(store=self.store).update(square_application_id="")
        st = self.c.get("/api/terminals/status").json()
        self.assertEqual(st["mode"], "incomplete")
        self.assertFalse(st["enabled"])
        v = self.open_visit()
        self.assertEqual(self.send(v["id"]).status_code, 409)

    def test_ok_without_token_completes_once_unverified(self):
        v, tp, d = self.start()
        r = self.callback(status="ok", transaction_id="TXN-1", client_transaction_id="CL-1", state=d["state"])
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["status"], "completed")
        self.assertEqual(r.json()["visit"], v["id"])
        co = Checkout.objects.get(visit_id=v["id"])
        self.assertEqual(r.json()["checkout"], co.id)
        pay = Payment.objects.get(checkout=co)
        self.assertEqual((pay.method, pay.amount, pay.card_reference), ("card", Decimal(5800), "TXN-1"))
        self.assertIn("照合していない", pay.note)
        # Safari の再読み込みなどで何度届いても会計は 1 つ
        self.assertEqual(self.callback(status="ok", transaction_id="TXN-1", state=d["state"]).json()["status"], "completed")
        self.assertEqual(Checkout.objects.filter(visit_id=v["id"]).count(), 1)
        # 元の画面（数秒おきの確認）でも完了が見える。もう Square のアプリを開く URL は出さない
        polled = self.c.get(f"/api/terminal-payments/{tp['id']}").json()
        self.assertEqual(polled["status"], "completed")
        self.assertIsNone(polled["handoff_url"])

    def test_ok_is_verified_with_square_orders_api(self):
        TerminalConfig.objects.filter(store=self.store).update(square_access_token="prod-token", square_location_id="LOC1")
        v, tp, d = self.start()
        self.assertEqual(d["location_id"], "LOC1")
        calls = []

        def _open(req, timeout=None):
            calls.append(req.full_url)
            return FakeResponse({"order": {"id": "TXN-2", "state": "COMPLETED", "location_id": "LOC1", "total_money": {"amount": 5800, "currency": "JPY"}}})

        with mock.patch("apps.terminals.providers.urllib.request.urlopen", side_effect=_open):
            r = self.callback(status="ok", transaction_id="TXN-2", state=d["state"])
        self.assertEqual(r.json()["status"], "completed")
        self.assertEqual(calls, ["https://connect.squareup.com/v2/orders/TXN-2"])
        self.assertTrue(TerminalPayment.objects.get(id=tp["id"]).provider_verified)
        self.assertNotIn("照合していない", Payment.objects.get(checkout__visit_id=v["id"]).note)

    def test_amount_mismatch_needs_attention_without_checkout(self):
        TerminalConfig.objects.filter(store=self.store).update(square_access_token="prod-token")
        v, tp, d = self.start()
        resp = {"order": {"id": "TXN-3", "state": "COMPLETED", "total_money": {"amount": 1000, "currency": "JPY"}}}
        with mock.patch("apps.terminals.providers.urllib.request.urlopen", return_value=FakeResponse(resp)):
            r = self.callback(status="ok", transaction_id="TXN-3", state=d["state"])
        self.assertEqual(r.json()["status"], "needs_attention")
        self.assertIn("金額", r.json()["message"])
        self.assertFalse(Checkout.objects.filter(visit_id=v["id"]).exists())

    def test_canceled_in_square_app_then_resend(self):
        v, tp, d = self.start()
        r = self.callback(status="error", error_code="payment_canceled", state=d["state"])
        self.assertEqual(r.json()["status"], "canceled")
        self.assertIn("キャンセル", r.json()["message"])
        self.assertEqual(self.send(v["id"]).status_code, 201)

    def test_error_codes_are_explained(self):
        _, tp, d = self.start()
        r = self.callback(status="error", error_code="not_logged_in", state=d["state"])
        self.assertEqual(r.json()["status"], "declined")
        self.assertIn("ログイン", r.json()["message"])

    def test_paid_in_square_after_cancel_in_noxia_needs_attention(self):
        v, tp, d = self.start()
        self.assertEqual(self.c.post(f"/api/terminal-payments/{tp['id']}/cancel", {}, format="json").json()["status"], "canceled")
        r = self.callback(status="ok", transaction_id="TXN-4", state=d["state"])
        self.assertEqual(r.json()["status"], "needs_attention")
        self.assertIn("返金", r.json()["message"])
        self.assertFalse(Checkout.objects.filter(visit_id=v["id"]).exists())

    def test_unknown_or_missing_state_is_refused(self):
        self.start()
        self.assertEqual(self.callback(status="ok", transaction_id="X", state="x" * 43).status_code, 404)
        self.assertEqual(self.callback(status="ok", transaction_id="X").status_code, 404)
        self.assertEqual(self.public.post("/api/terminals/square-pos/callback", {"data": "not json"}, format="json").status_code, 400)

    def test_other_store_cannot_see_or_pay(self):
        _, tp, d = self.start()
        other = make_store("s2", company=self.fx.company)
        oc = client_for(self.fx.users["owner"], other.store)
        self.assertEqual(oc.get(f"/api/terminal-payments/{tp['id']}").status_code, 404)
        self.assertEqual(oc.post(f"/api/terminal-payments/{tp['id']}/cancel", {}, format="json").status_code, 404)
        # 別店舗の端末 ID を指定して送れない
        ov = oc.post("/api/visits", {"table_id": other.tables[0].id, "entries": [{"guest_count": 1, "course_id": other.hourly.id}]}, format="json", **key()).json()
        TerminalConfig.objects.create(store=other.store, provider="square_pos", square_application_id="sq0idp-OTHER", square_pos_callback_url=CALLBACK)
        r = oc.post(f"/api/visits/{ov['id']}/terminal-payments", {"terminal_id": self.terminal.id}, format="json", **key())
        self.assertEqual(r.status_code, 400)

    def test_callback_url_must_be_https(self):
        r = self.c.put("/api/terminals/config", {"square_pos_callback_url": "http://example.com/cb"}, format="json")
        self.assertEqual(r.status_code, 400)
        r = self.c.put("/api/terminals/config", {"square_pos_callback_url": CALLBACK, "square_application_id": " sq0idp-NEW "}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["square_application_id"], "sq0idp-NEW")
        self.assertEqual(r.json()["mode"], "square_pos")
