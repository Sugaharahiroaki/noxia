"""カード決済端末連携: 模擬端末での会計・二重請求の防止・権限・Square の送信内容と通知の署名。"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import uuid
from decimal import Decimal
from unittest import mock

from django.test import override_settings
from rest_framework.test import APITestCase

from apps.cash.models import BusinessDay
from apps.cash.services import precheck
from apps.core.testing import client_for, standard_fixture
from apps.sales.models import Checkout, OrderLine, Payment
from apps.terminals.models import CardTerminal, TerminalConfig, TerminalPayment, TerminalWebhookEvent
from apps.terminals.providers import verify_square_signature


def key():
    return {"HTTP_IDEMPOTENCY_KEY": str(uuid.uuid4())}


class TerminalBase(APITestCase):
    def setUp(self):
        self.fx = standard_fixture()
        self.store = self.fx.store
        self.c = client_for(self.fx.users["manager"], self.store)
        TerminalConfig.objects.create(store=self.store, provider="mock")
        self.terminal = CardTerminal.objects.create(store=self.store, provider="mock", name="レジ横", device_id="mock_device_1", status="paired")

    def open_visit(self, beers=1):
        r = self.c.post("/api/visits", {"table_id": self.fx.tables[0].id, "entries": [{"guest_count": 2, "course_id": self.fx.hourly.id}]}, format="json", **key())
        self.assertEqual(r.status_code, 201, r.content)
        v = r.json()
        if beers:
            r = self.c.post(f"/api/visits/{v['id']}/lines", {"kind": "product", "product_id": self.fx.products["beer"].id, "quantity": beers}, format="json", **key())
            self.assertEqual(r.status_code, 201, r.content)
        return v  # セット 2,500×2 + ビール 800×beers

    def send(self, visit_id, **body):
        body.setdefault("terminal_id", self.terminal.id)
        return self.c.post(f"/api/visits/{visit_id}/terminal-payments", body, format="json", **key())


class MockTerminalFlowTests(TerminalBase):
    def test_status_lists_paired_terminals(self):
        r = self.c.get("/api/terminals/status")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["enabled"])
        self.assertTrue(r.json()["is_mock"])
        self.assertEqual([t["name"] for t in r.json()["terminals"]], ["レジ横"])

    def test_approve_creates_card_checkout_once(self):
        v = self.open_visit()
        r = self.send(v["id"], card_amount="5800")
        self.assertEqual(r.status_code, 201, r.content)
        tp = r.json()
        self.assertEqual(tp["status"], "pending")
        self.assertEqual(Decimal(tp["amount"]), Decimal(5800))

        r = self.c.post(f"/api/terminal-payments/{tp['id']}/simulate", {"result": "approve"}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["status"], "completed")
        self.assertIsNotNone(r.json()["checkout_number"])
        checkout = Checkout.objects.get(visit_id=v["id"])
        self.assertEqual(checkout.total, Decimal(5800))
        pay = Payment.objects.get(checkout=checkout)
        self.assertEqual(pay.method, "card")
        self.assertTrue(pay.card_reference.startswith("MOCK-"))
        self.assertEqual(checkout.visit.status, "closed")

        # 確認を何度呼んでも会計は1つ
        for _ in range(3):
            r = self.c.get(f"/api/terminal-payments/{tp['id']}")
            self.assertEqual(r.json()["status"], "completed")
        self.assertEqual(Checkout.objects.filter(visit_id=v["id"]).count(), 1)

    def test_decline_keeps_visit_open_and_can_resend(self):
        v = self.open_visit()
        tp = self.send(v["id"]).json()
        r = self.c.post(f"/api/terminal-payments/{tp['id']}/simulate", {"result": "decline"}, format="json")
        self.assertEqual(r.json()["status"], "declined")
        self.assertIn("拒否", r.json()["error_message"])
        self.assertFalse(Checkout.objects.filter(visit_id=v["id"]).exists())
        self.assertEqual(self.send(v["id"]).status_code, 201)

    def test_cancel_then_manual_checkout_is_possible(self):
        v = self.open_visit()
        tp = self.send(v["id"]).json()
        r = self.c.post(f"/api/terminal-payments/{tp['id']}/cancel", {}, format="json")
        self.assertEqual(r.json()["status"], "canceled")
        r = self.c.post(f"/api/visits/{v['id']}/checkouts", {"payments": [{"method": "cash", "amount": "5800", "tendered": "10000"}]}, format="json", **key())
        self.assertEqual(r.status_code, 201, r.content)

    def test_while_pending_manual_checkout_and_second_send_are_blocked(self):
        v = self.open_visit()
        tp = self.send(v["id"]).json()
        r = self.c.post(f"/api/visits/{v['id']}/checkouts", {"payments": [{"method": "card", "amount": "5800"}]}, format="json", **key())
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["error"]["code"], "terminal_busy")
        r = self.send(v["id"])
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["error"]["code"], "terminal_busy")
        # 送った明細は取り消せない
        line_id = tp["line_ids"][0]
        r = self.c.post(f"/api/visits/{v['id']}/lines/{line_id}/cancel", {"reason": "誤入力"}, format="json", **key())
        self.assertEqual(r.status_code, 409)
        r = self.c.post(f"/api/visits/{v['id']}/cancel", {"reason": "誤入力"}, format="json", **key())
        self.assertEqual(r.status_code, 409)

    def test_lines_added_while_pending_stay_on_the_slip(self):
        v = self.open_visit()
        tp = self.send(v["id"]).json()
        r = self.c.post(f"/api/visits/{v['id']}/lines/batch", {"items": [{"kind": "product", "product_id": self.fx.products["beer"].id, "quantity": 1}]},
                        format="json", **key())
        self.assertEqual(r.status_code, 201, r.content)
        new_line = r.json()["created_line_ids"][0]
        self.assertNotIn(new_line, tp["line_ids"])
        self.c.post(f"/api/terminal-payments/{tp['id']}/simulate", {"result": "approve"}, format="json")
        checkout = Checkout.objects.get(visit_id=v["id"])
        self.assertEqual(checkout.total, Decimal(5800))
        self.assertIsNone(OrderLine.objects.get(id=new_line).checkout_id)
        self.assertEqual(checkout.visit.status, "open")  # 追加分が残っている

    def test_mixed_cash_and_card(self):
        v = self.open_visit()
        r = self.send(v["id"], cash_amount="3000", cash_tendered="5000", card_amount="2800")
        self.assertEqual(r.status_code, 201, r.content)
        tp = r.json()
        self.assertEqual(Decimal(tp["amount"]), Decimal(2800))
        self.assertEqual(Decimal(tp["change_amount"]), Decimal(2000))
        self.c.post(f"/api/terminal-payments/{tp['id']}/simulate", {"result": "approve"}, format="json")
        pays = {p.method: p for p in Payment.objects.filter(checkout__visit_id=v["id"])}
        self.assertEqual(pays["card"].amount, Decimal(2800))
        self.assertEqual(pays["cash"].amount, Decimal(3000))
        self.assertEqual(pays["cash"].change_amount, Decimal(2000))

    def test_amount_checks(self):
        v = self.open_visit()
        r = self.send(v["id"], card_amount="5000")  # 画面が古い
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["error"]["code"], "amount_changed")
        r = self.send(v["id"], cash_amount="3000", cash_tendered="2000")
        self.assertEqual(r.status_code, 400)
        r = self.send(v["id"], cash_amount="5800", cash_tendered="6000")
        self.assertEqual(r.status_code, 400)  # カード分が0円
        self.assertFalse(TerminalPayment.objects.exists())

    def test_requires_open_register(self):
        v = self.open_visit()
        BusinessDay.objects.filter(store=self.store).update(status="closed")
        r = self.send(v["id"])
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["error"]["code"], "register_not_open")

    def test_approved_but_checkout_fails_needs_attention(self):
        v = self.open_visit()
        tp = self.send(v["id"]).json()
        # 何らかの理由で送った明細が会計できなくなった状態を作る
        OrderLine.objects.filter(id=tp["line_ids"][0]).update(status="cancelled")
        r = self.c.post(f"/api/terminal-payments/{tp['id']}/simulate", {"result": "approve"}, format="json")
        self.assertEqual(r.json()["status"], "needs_attention")
        self.assertFalse(Checkout.objects.filter(visit_id=v["id"]).exists())
        self.assertEqual(len(self.c.get("/api/terminals/status").json()["needs_attention"]), 1)
        day = BusinessDay.objects.get(store=self.store, status="open")
        self.assertIn("terminal_attention", [w["code"] for w in precheck(day)])
        r = self.c.post(f"/api/terminal-payments/{tp['id']}/resolve", {"note": ""}, format="json")
        self.assertEqual(r.status_code, 400)
        r = self.c.post(f"/api/terminal-payments/{tp['id']}/resolve", {"note": "端末で返金した"}, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.c.get("/api/terminals/status").json()["needs_attention"], [])
        self.assertNotIn("terminal_attention", [w["code"] for w in precheck(day)])

    def test_resume_shows_active_payment(self):
        v = self.open_visit()
        tp = self.send(v["id"]).json()
        r = self.c.get(f"/api/visits/{v['id']}/terminal-payments")
        self.assertEqual(r.json()["active"]["id"], tp["id"])

    @override_settings(CARD_TERMINAL_MOCK_ENABLED=False)
    def test_mock_can_be_disabled_on_production_servers(self):
        v = self.open_visit()
        r = self.send(v["id"])
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["error"]["code"], "terminal_mock_disabled")
        self.assertFalse(self.c.get("/api/terminals/status").json()["enabled"])


class TerminalPermissionTests(TerminalBase):
    def test_cast_cannot_use_terminal(self):
        v = self.open_visit()
        cast = client_for(self.fx.users["cast"], self.store)
        r = cast.post(f"/api/visits/{v['id']}/terminal-payments", {"terminal_id": self.terminal.id}, format="json", **key())
        self.assertEqual(r.status_code, 403)

    def test_other_store_cannot_see_or_touch(self):
        v = self.open_visit()
        tp = self.send(v["id"]).json()
        other = standard_fixture("s2", company_code="c2")
        oc = client_for(other.users["manager"], other.store)
        self.assertEqual(oc.get(f"/api/terminal-payments/{tp['id']}").status_code, 404)
        self.assertEqual(oc.post(f"/api/terminal-payments/{tp['id']}/simulate", {"result": "approve"}, format="json").status_code, 404)
        # 自店の伝票に他店の端末は使えない
        TerminalConfig.objects.create(store=other.store, provider="mock")
        r = oc.post(f"/api/visits/{v['id']}/terminal-payments", {"terminal_id": self.terminal.id}, format="json", **key())
        self.assertIn(r.status_code, (400, 404))
        self.assertEqual(TerminalPayment.objects.get(id=tp["id"]).status, "pending")

    def test_config_requires_settings_edit_and_masks_secrets(self):
        staff = client_for(self.fx.users["staff"], self.store)
        self.assertEqual(staff.put("/api/terminals/config", {"provider": "none"}, format="json").status_code, 403)
        no_reauth = client_for(self.fx.users["manager"], self.store, reauth=False)
        self.assertEqual(no_reauth.put("/api/terminals/config", {"provider": "none"}, format="json").status_code, 403)
        r = self.c.put("/api/terminals/config", {"provider": "square", "square_environment": "sandbox", "square_location_id": "L1",
                                                 "square_access_token": "EAAAsecret-token-9876"}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertNotIn("EAAAsecret", json.dumps(body))
        self.assertEqual(body["square_access_token_masked"], "******9876")
        self.assertEqual(body["mode"], "sandbox")
        # 空文字は「変更しない」
        self.c.put("/api/terminals/config", {"square_access_token": ""}, format="json")
        self.assertEqual(TerminalConfig.objects.get(store=self.store).square_access_token, "EAAAsecret-token-9876")

    def test_add_mock_terminal_is_paired(self):
        r = self.c.post("/api/terminals/devices", {"name": "カウンター"}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()["status"], "paired")
        self.assertEqual(self.c.post("/api/terminals/devices", {"name": "カウンター"}, format="json").status_code, 400)


class FakeResponse:
    def __init__(self, data):
        self.data = json.dumps(data).encode("utf-8")

    def read(self):
        return self.data

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class SquareTests(TerminalBase):
    def setUp(self):
        super().setUp()
        TerminalConfig.objects.filter(store=self.store).update(
            provider="square", square_environment="sandbox", square_access_token="sandbox-token", square_location_id="LOC1",
            square_webhook_signature_key="sig-key", square_webhook_url="https://example.test/api/terminals/webhook/x",
        )
        self.terminal = CardTerminal.objects.create(store=self.store, provider="square", name="Square端末", device_id="DEV-1", status="paired")
        self.calls = []

    def fake_urlopen(self, responses):
        def _open(req, timeout=None):
            self.calls.append({"url": req.full_url, "method": req.get_method(), "body": json.loads(req.data) if req.data else None,
                               "auth": req.get_header("Authorization")})
            return FakeResponse(responses.pop(0))
        return mock.patch("apps.terminals.providers.urllib.request.urlopen", side_effect=_open)

    def test_create_checkout_payload_and_poll_completion(self):
        v = self.open_visit()
        with self.fake_urlopen([{"checkout": {"id": "CHK1", "status": "PENDING"}}]):
            r = self.send(v["id"])
        self.assertEqual(r.status_code, 201, r.content)
        call = self.calls[0]
        self.assertEqual(call["url"], "https://connect.squareupsandbox.com/v2/terminals/checkouts")
        self.assertEqual(call["auth"], "Bearer sandbox-token")
        self.assertEqual(call["body"]["checkout"]["amount_money"], {"amount": 5800, "currency": "JPY"})
        self.assertEqual(call["body"]["checkout"]["device_options"]["device_id"], "DEV-1")
        tp = r.json()
        with self.fake_urlopen([{"checkout": {"id": "CHK1", "status": "IN_PROGRESS"}}]):
            self.assertEqual(self.c.get(f"/api/terminal-payments/{tp['id']}").json()["status"], "pending")
        with self.fake_urlopen([{"checkout": {"id": "CHK1", "status": "COMPLETED", "payment_ids": ["PAY-123"]}}]):
            r = self.c.get(f"/api/terminal-payments/{tp['id']}")
        self.assertEqual(r.json()["status"], "completed")
        self.assertEqual(Payment.objects.get(checkout__visit_id=v["id"]).card_reference, "PAY-123")
        # simulate は Square では使えない
        self.assertEqual(self.c.post(f"/api/terminal-payments/{tp['id']}/simulate", {"result": "approve"}, format="json").status_code, 403)

    def test_production_is_blocked_without_live_flag(self):
        TerminalConfig.objects.filter(store=self.store).update(square_environment="production")
        v = self.open_visit()
        with self.fake_urlopen([]):
            r = self.send(v["id"])
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["error"]["code"], "terminal_live_disabled")
        self.assertEqual(self.calls, [])
        self.assertEqual(self.c.get("/api/terminals/status").json()["mode"], "production_blocked")

    @override_settings(CARD_TERMINAL_LIVE_ENABLED=True)
    def test_production_uses_live_endpoint_when_allowed(self):
        TerminalConfig.objects.filter(store=self.store).update(square_environment="production")
        v = self.open_visit()
        with self.fake_urlopen([{"checkout": {"id": "CHK2", "status": "PENDING"}}]):
            self.assertEqual(self.send(v["id"]).status_code, 201)
        self.assertTrue(self.calls[0]["url"].startswith("https://connect.squareup.com/"))

    def _webhook(self, payload, signature=None):
        body = json.dumps(payload).encode("utf-8")
        cfg = TerminalConfig.objects.get(store=self.store)
        if signature is None:
            mac = hmac.new(b"sig-key", cfg.square_webhook_url.encode("utf-8") + body, hashlib.sha256).digest()
            signature = base64.b64encode(mac).decode()
        return self.client.post(f"/api/terminals/webhook/{self.store.webhook_token}", body, content_type="application/json",
                                HTTP_X_SQUARE_HMACSHA256_SIGNATURE=signature)

    def test_webhook_completes_checkout_once(self):
        v = self.open_visit()
        with self.fake_urlopen([{"checkout": {"id": "CHK9", "status": "PENDING"}}]):
            tp = self.send(v["id"]).json()
        payload = {"event_id": "EV1", "type": "terminal.checkout.updated",
                   "data": {"object": {"checkout": {"id": "CHK9", "status": "COMPLETED", "payment_ids": ["PAY-9"]}}}}
        self.assertEqual(self._webhook(payload, signature="bad").status_code, 401)
        self.assertEqual(TerminalPayment.objects.get(id=tp["id"]).status, "pending")
        self.assertEqual(self._webhook(payload).status_code, 200)
        self.assertEqual(self._webhook(payload).json().get("duplicate"), True)
        self.assertEqual(TerminalPayment.objects.get(id=tp["id"]).status, "completed")
        self.assertEqual(Checkout.objects.filter(visit_id=v["id"]).count(), 1)
        self.assertEqual(TerminalWebhookEvent.objects.count(), 1)

    def test_pairing(self):
        t = CardTerminal.objects.create(store=self.store, provider="square", name="新しい端末")
        with self.fake_urlopen([{"device_code": {"id": "DC1", "code": "ABCDEF", "status": "UNPAIRED"}}]):
            r = self.c.post(f"/api/terminals/devices/{t.id}/pair", {}, format="json")
        self.assertEqual(r.json()["pairing_code"], "ABCDEF")
        self.assertEqual(self.calls[0]["body"]["device_code"]["product_type"], "TERMINAL_API")
        with self.fake_urlopen([{"device_code": {"id": "DC1", "status": "PAIRED", "device_id": "DEV-NEW"}}]):
            r = self.c.get(f"/api/terminals/devices/{t.id}/pair")
        self.assertEqual(r.json()["status"], "paired")
        self.assertEqual(r.json()["device_id"], "DEV-NEW")

    def test_signature_helper(self):
        mac = base64.b64encode(hmac.new(b"k", b"https://a/b" + b"{}", hashlib.sha256).digest()).decode()
        self.assertTrue(verify_square_signature("k", "https://a/b", b"{}", mac))
        self.assertFalse(verify_square_signature("k", "https://a/c", b"{}", mac))
        self.assertFalse(verify_square_signature("", "https://a/b", b"{}", mac))
