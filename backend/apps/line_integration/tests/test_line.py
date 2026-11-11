"""LINE連携: モック送信・送信前確認・同意・Webhook署名と重複排除・実送信の抑止。"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import uuid
from unittest import mock

from django.test import override_settings
from rest_framework.test import APIClient, APITestCase

from apps.core.testing import client_for, standard_fixture
from apps.customers.models import Customer
from apps.line_integration.models import Inquiry, LineIntegration, LineUser, MessageDraft, MessageTemplate, WebhookEvent
from apps.line_integration.provider import LineMessagingProvider, MockProvider, get_provider


def key():
    return {"HTTP_IDEMPOTENCY_KEY": str(uuid.uuid4())}


def sign(secret: str, body: bytes) -> str:
    return base64.b64encode(hmac.new(secret.encode(), body, hashlib.sha256).digest()).decode()


class LineBase(APITestCase):
    def setUp(self):
        self.fx = standard_fixture()
        self.store = self.fx.store
        self.manager = client_for(self.fx.users["manager"], self.store)
        self.staff = client_for(self.fx.users["staff"], self.store)
        self.customer = Customer.objects.create(company=self.fx.company, store=self.store, display_name="田中様")
        self.lu = LineUser.objects.create(store=self.store, line_user_id="U" + "1" * 32, display_name="たなか", consent="opted_in", customer=self.customer)
        self.tpl = MessageTemplate.objects.create(store=self.store, name="お礼", kind="thanks", body="{name}、ありがとうございました。{store}")


class SendTests(LineBase):
    def _draft(self, body="", template=None, line_user=None):
        payload = {"line_user": (line_user or self.lu).id, "body": body}
        if template:
            payload["template"] = template.id
        r = self.manager.post("/api/line/drafts", payload, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        return r.json()

    def test_integration_not_connected_by_default(self):
        r = self.manager.get("/api/line/integration")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "not_connected")
        self.assertFalse(r.json()["has_credentials"])
        self.assertEqual(r.json()["effective_send_mode"], "mock")
        self.assertEqual(self.staff.get("/api/line/integration").status_code, 200)
        self.assertEqual(self.staff.put("/api/line/integration", {"channel_id": "x"}, format="json").status_code, 403)

    def test_draft_review_send_is_mock_without_credentials(self):
        d = self._draft(template=self.tpl)
        self.assertEqual(d["body"], "田中様、ありがとうございました。" + self.store.name)
        self.assertEqual(d["status"], "draft")
        with mock.patch("apps.line_integration.provider.urllib.request.urlopen") as urlopen:
            r = self.manager.post(f"/api/line/drafts/{d['id']}/send", {}, format="json", **key())
            self.assertEqual(r.status_code, 409)  # 送信前確認が済んでいない
            r = self.manager.post(f"/api/line/drafts/{d['id']}/review", {}, format="json")
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.json()["status"], "reviewed")
            r = self.manager.post(f"/api/line/drafts/{d['id']}/send", {}, format="json", **key())
            self.assertEqual(r.status_code, 200, r.content)
            self.assertEqual(r.json()["status"], "mock_sent")
            self.assertTrue(r.json()["result"]["mock"])
            self.assertTrue(r.json()["result"]["ok"])
            urlopen.assert_not_called()
        draft = MessageDraft.objects.get(id=d["id"])
        self.assertEqual(draft.sent_by, self.fx.users["manager"])
        self.assertIsNotNone(draft.sent_at)
        # 送信済みは編集不可
        self.assertEqual(self.manager.patch(f"/api/line/drafts/{d['id']}", {"body": "x"}, format="json").status_code, 409)
        # スタッフは送信・管理不可（閲覧のみ）
        self.assertEqual(self.staff.get("/api/line/drafts").status_code, 200)
        self.assertEqual(self.staff.post("/api/line/drafts", {"line_user": self.lu.id, "body": "x"}, format="json").status_code, 403)

    def test_send_blocked_without_consent(self):
        opted_out = LineUser.objects.create(store=self.store, line_user_id="U" + "2" * 32, consent="opted_out")
        d = self._draft(body="こんにちは", line_user=opted_out)
        self.manager.post(f"/api/line/drafts/{d['id']}/review", {}, format="json")
        with mock.patch("apps.line_integration.provider.urllib.request.urlopen") as urlopen:
            r = self.manager.post(f"/api/line/drafts/{d['id']}/send", {}, format="json", **key())
            urlopen.assert_not_called()
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["status"], "blocked")
        unknown = LineUser.objects.create(store=self.store, line_user_id="U" + "3" * 32, consent="unknown")
        d = self._draft(body="x", line_user=unknown)
        self.manager.post(f"/api/line/drafts/{d['id']}/review", {}, format="json")
        self.assertEqual(self.manager.post(f"/api/line/drafts/{d['id']}/send", {}, format="json", **key()).status_code, 409)

    @override_settings(LINE_SEND_ENABLED=False)
    def test_real_provider_not_used_when_env_disabled(self):
        integ = LineIntegration.objects.create(store=self.store, channel_id="1", channel_secret="s", channel_access_token="t",
                                               status=LineIntegration.STATUS_CONNECTED, send_enabled=True)
        self.assertIsInstance(get_provider(integ), MockProvider)
        d = self._draft(body="テスト")
        self.manager.post(f"/api/line/drafts/{d['id']}/review", {}, format="json")
        with mock.patch("apps.line_integration.provider.urllib.request.urlopen") as urlopen:
            r = self.manager.post(f"/api/line/drafts/{d['id']}/send", {}, format="json", **key())
            urlopen.assert_not_called()
        self.assertEqual(r.json()["status"], "mock_sent")
        self.assertEqual(self.manager.get("/api/line/integration").json()["effective_send_mode"], "mock")

    @override_settings(LINE_SEND_ENABLED=True)
    def test_real_provider_requires_all_conditions(self):
        integ = LineIntegration.objects.create(store=self.store, channel_id="1", channel_secret="s", channel_access_token="t",
                                               status=LineIntegration.STATUS_CONNECTED, send_enabled=False)
        self.assertIsInstance(get_provider(integ), MockProvider)
        integ.send_enabled = True
        self.assertIsInstance(get_provider(integ), LineMessagingProvider)
        integ.channel_access_token = ""
        self.assertIsInstance(get_provider(integ), MockProvider)

    def test_secrets_masked_in_api(self):
        r = self.manager.put("/api/line/integration", {"channel_id": "123", "channel_secret": "supersecretvalue", "channel_access_token": "tokentokentoken", "send_enabled": False}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        d = r.json()
        self.assertNotIn("channel_secret", d)
        self.assertNotIn("channel_access_token", d)
        self.assertTrue(d["channel_secret_masked"].endswith("alue"))
        self.assertTrue(d["channel_secret_masked"].startswith("***"))
        self.assertTrue(d["has_credentials"])
        self.assertEqual(d["status"], "connected")
        self.assertEqual(d["effective_send_mode"], "mock")
        # 空文字で更新してもシークレットは消えない
        r = self.manager.put("/api/line/integration", {"channel_secret": "", "note": "x"}, format="json")
        self.assertTrue(r.json()["has_credentials"])
        self.assertEqual(LineIntegration.objects.get(store=self.store).channel_secret, "supersecretvalue")

    def test_explicit_customer_link(self):
        lu = LineUser.objects.create(store=self.store, line_user_id="U" + "4" * 32, display_name="x")
        r = self.manager.patch(f"/api/line/users/{lu.id}", {"customer": self.customer.id, "consent": "opted_in"}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        lu.refresh_from_db()
        self.assertEqual(lu.customer_id, self.customer.id)
        self.assertEqual(lu.linked_by, self.fx.users["manager"])
        self.assertIsNotNone(lu.linked_at)
        self.assertIsNotNone(lu.consent_changed_at)
        # 別会社の顧客には紐付けできない
        other = standard_fixture("z1", "cz")
        oc = Customer.objects.create(company=other.company, store=other.store, display_name="他社客")
        r = self.manager.patch(f"/api/line/users/{lu.id}", {"customer": oc.id}, format="json")
        self.assertEqual(r.status_code, 400)


class WebhookTests(LineBase):
    def setUp(self):
        super().setUp()
        self.secret = "channel-secret-xyz"
        LineIntegration.objects.create(store=self.store, channel_id="1", channel_secret=self.secret, channel_access_token="t", webhook_enabled=True)
        self.path = f"/api/line/webhook/{self.store.webhook_token}"
        self.anon = APIClient()

    def _post(self, payload: dict, signature: str | None = None):
        body = json.dumps(payload).encode()
        sig = signature if signature is not None else sign(self.secret, body)
        return self.anon.generic("POST", self.path, data=body, content_type="application/json", HTTP_X_LINE_SIGNATURE=sig)

    def _event(self, event_id, etype="message", text="予約したいです", uid="U" + "9" * 32):
        ev = {"type": etype, "webhookEventId": event_id, "timestamp": 1, "source": {"type": "user", "userId": uid}}
        if etype == "message":
            ev["message"] = {"type": "text", "id": "m1", "text": text}
        return ev

    def test_bad_signature_rejected(self):
        r = self._post({"events": [self._event("e1")]}, signature="invalid")
        self.assertEqual(r.status_code, 401)
        self.assertEqual(WebhookEvent.objects.count(), 0)
        r = self._post({"events": [self._event("e1")]}, signature="")
        self.assertEqual(r.status_code, 401)
        r = self.anon.post(f"/api/line/webhook/{'x' * 10}", {}, format="json")
        self.assertEqual(r.status_code, 404)

    def test_valid_event_creates_user_and_inquiry_and_dedupes(self):
        r = self._post({"events": [self._event("e1"), self._event("e2", etype="follow", uid="U" + "8" * 32)]})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(WebhookEvent.objects.filter(store=self.store).count(), 2)
        lu = LineUser.objects.get(store=self.store, line_user_id="U" + "9" * 32)
        self.assertIsNone(lu.customer_id)  # 自動では紐付けない
        self.assertEqual(lu.consent, "unknown")
        inq = Inquiry.objects.get(store=self.store, line_user=lu)
        self.assertEqual(inq.text, "予約したいです")
        self.assertEqual(inq.status, "open")
        self.assertTrue(LineUser.objects.get(store=self.store, line_user_id="U" + "8" * 32).is_following)
        # 再送（同じ webhookEventId）は無視される
        r = self._post({"events": [self._event("e1")]})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(WebhookEvent.objects.filter(store=self.store).count(), 2)
        self.assertEqual(Inquiry.objects.count(), 1)
        # unfollow で配信停止
        r = self._post({"events": [self._event("e3", etype="unfollow")]})
        lu.refresh_from_db()
        self.assertFalse(lu.is_following)
        self.assertEqual(lu.consent, "opted_out")
        # 管理画面から問い合わせ対応
        r = self.manager.get("/api/line/inquiries")
        self.assertEqual(len(r.json()), 1)
        r = self.manager.patch(f"/api/line/inquiries/{inq.id}", {"status": "handled"}, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertIsNotNone(Inquiry.objects.get(id=inq.id).handled_at)
        self.assertEqual(self.manager.get("/api/line/webhook-events").status_code, 200)

    def test_webhook_disabled_or_other_store_token(self):
        LineIntegration.objects.filter(store=self.store).update(webhook_enabled=False)
        self.assertEqual(self._post({"events": [self._event("e1")]}).status_code, 404)
        other = standard_fixture("z1", "cz")
        LineIntegration.objects.create(store=other.store, channel_id="1", channel_secret="other-secret", channel_access_token="t", webhook_enabled=True)
        body = json.dumps({"events": [self._event("e9")]}).encode()
        # 店舗 A の秘密鍵で署名したものを店舗 B のパスに送っても拒否される
        r = self.anon.generic("POST", f"/api/line/webhook/{other.store.webhook_token}", data=body, content_type="application/json", HTTP_X_LINE_SIGNATURE=sign(self.secret, body))
        self.assertEqual(r.status_code, 401)
        self.assertEqual(LineUser.objects.filter(store=other.store).count(), 0)
