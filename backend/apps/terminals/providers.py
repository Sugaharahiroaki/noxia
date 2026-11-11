"""
決済会社ごとの「接続部品」。会計の流れ（services.py）はこの共通の形だけを使う。

  create_checkout(terminal, amount, reference, idempotency_key) -> ProviderCheckout
  get_checkout(checkout_id) -> ProviderCheckout
  cancel_checkout(checkout_id) -> ProviderCheckout
  create_pairing_code(name, idempotency_key) -> (code_id, code, expires_at_iso)
  get_pairing_status(code_id) -> device_id | None

状態は共通の値に揃える: PENDING / COMPLETED / CANCELED / FAILED

- MockProvider（模擬端末）: 実際の決済はしない。画面の「承認／拒否（テスト）」で結果を決める。
- SquareTerminalProvider: Square の Terminal API（https://developer.squareup.com/docs/terminal-api/overview）。
  本番（production）への送信は CARD_TERMINAL_LIVE_ENABLED=1 のときだけ。Sandbox は練習用なので許可する。
  新しい決済会社に対応するときは、この形のクラスを1つ足す。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from decimal import Decimal

from django.conf import settings

from apps.core.exceptions import ApiError

log = logging.getLogger("yorumiru.terminals")


@dataclass
class ProviderCheckout:
    checkout_id: str
    status: str  # PENDING / COMPLETED / CANCELED / FAILED
    payment_ids: list[str] = field(default_factory=list)
    detail: str = ""


class MockProvider:
    """模擬端末（テスト用）。端末への送信は記録するだけ。結果は services.simulate で決める。"""

    name = "mock"
    is_mock = True

    def create_checkout(self, terminal, amount: Decimal, reference: str, idempotency_key: str) -> ProviderCheckout:
        return ProviderCheckout(checkout_id=f"mock_{uuid.uuid4().hex[:16]}", status="PENDING")

    def get_checkout(self, checkout_id: str) -> ProviderCheckout | None:
        return None  # 模擬端末の状態は TerminalPayment 自身が持つ

    def cancel_checkout(self, checkout_id: str) -> ProviderCheckout:
        return ProviderCheckout(checkout_id=checkout_id, status="CANCELED")

    def create_pairing_code(self, name: str, idempotency_key: str):
        return (f"mock_code_{uuid.uuid4().hex[:8]}", "MOCK", None)

    def get_pairing_status(self, code_id: str) -> str | None:
        return f"mock_device_{code_id[-8:]}"


SQUARE_BASE = {"sandbox": "https://connect.squareupsandbox.com", "production": "https://connect.squareup.com"}
SQUARE_STATUS = {"PENDING": "PENDING", "IN_PROGRESS": "PENDING", "CANCEL_REQUESTED": "PENDING", "COMPLETED": "COMPLETED", "CANCELED": "CANCELED"}


class SquareTerminalProvider:
    """Square ターミナル（Terminal API）。金額は日本円（最小単位＝1円）で送る。"""

    name = "square"
    is_mock = False

    def __init__(self, config):
        self.config = config
        env = config.square_environment if config.square_environment in SQUARE_BASE else "sandbox"
        if env == "production" and not settings.CARD_TERMINAL_LIVE_ENABLED:
            raise ApiError(
                "terminal_live_disabled",
                "本番のカード決済への送信は止めてあります（サーバーの CARD_TERMINAL_LIVE_ENABLED）。試験は Sandbox か模擬端末で行ってください。",
                status=409,
            )
        if not config.square_access_token or not config.square_location_id:
            raise ApiError("terminal_not_configured", "Square のアクセストークンとロケーションIDを店舗設定に入れてください。", status=409)
        self.base = SQUARE_BASE[env]

    def _call(self, method: str, path: str, body: dict | None = None) -> dict:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(
            self.base + path, data=data, method=method,
            headers={"Authorization": f"Bearer {self.config.square_access_token}", "Content-Type": "application/json", "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            log.warning("square api error status=%s path=%s", e.code, path)
            raise ApiError("terminal_provider_error", f"Square への送信でエラーが出ました（{e.code}）: {detail}", status=502)
        except (urllib.error.URLError, TimeoutError) as e:
            log.warning("square api unreachable: %s", type(e).__name__)
            raise ApiError("terminal_provider_unreachable", "Square に接続できませんでした。インターネット接続を確認してください。", status=502)

    @staticmethod
    def _to_checkout(raw: dict) -> ProviderCheckout:
        c = raw.get("checkout") or {}
        return ProviderCheckout(checkout_id=c.get("id", ""), status=SQUARE_STATUS.get(c.get("status", ""), "PENDING"),
                                payment_ids=list(c.get("payment_ids") or []), detail=c.get("cancel_reason", "") or "")

    def create_checkout(self, terminal, amount: Decimal, reference: str, idempotency_key: str) -> ProviderCheckout:
        if not terminal.device_id:
            raise ApiError("terminal_not_paired", "この端末はまだペアリングされていません。", status=409)
        body = {
            "idempotency_key": idempotency_key,
            "checkout": {
                "amount_money": {"amount": int(amount), "currency": "JPY"},
                "reference_id": reference[:40],
                "device_options": {"device_id": terminal.device_id},
                "note": "NOXIA",
            },
        }
        return self._to_checkout(self._call("POST", "/v2/terminals/checkouts", body))

    def get_checkout(self, checkout_id: str) -> ProviderCheckout:
        return self._to_checkout(self._call("GET", f"/v2/terminals/checkouts/{checkout_id}"))

    def cancel_checkout(self, checkout_id: str) -> ProviderCheckout:
        return self._to_checkout(self._call("POST", f"/v2/terminals/checkouts/{checkout_id}/cancel", {}))

    def create_pairing_code(self, name: str, idempotency_key: str):
        body = {"idempotency_key": idempotency_key, "device_code": {"product_type": "TERMINAL_API", "location_id": self.config.square_location_id, "name": name[:40]}}
        dc = self._call("POST", "/v2/devices/codes", body).get("device_code") or {}
        return (dc.get("id", ""), dc.get("code", ""), dc.get("pair_by"))

    def get_pairing_status(self, code_id: str) -> str | None:
        dc = self._call("GET", f"/v2/devices/codes/{code_id}").get("device_code") or {}
        return dc.get("device_id") if dc.get("status") == "PAIRED" else None


def verify_square_signature(signature_key: str, notification_url: str, body: bytes, signature: str) -> bool:
    """Square の通知の署名: base64(HMAC-SHA256(署名キー, 通知先URL + 本文))。"""
    if not signature_key or not signature or not notification_url:
        return False
    mac = hmac.new(signature_key.encode("utf-8"), notification_url.encode("utf-8") + body, hashlib.sha256).digest()
    return hmac.compare_digest(base64.b64encode(mac).decode("utf-8"), signature)


def get_provider(config):
    if config is None or config.provider == "none":
        raise ApiError("terminal_not_configured", "カード端末が未接続です（店舗設定 → カード端末）。端末に手入力で決済してください。", status=409)
    if config.provider == "mock":
        if not settings.CARD_TERMINAL_MOCK_ENABLED:
            raise ApiError("terminal_mock_disabled", "模擬端末はこのサーバーでは使えません（CARD_TERMINAL_MOCK_ENABLED）。", status=409)
        return MockProvider()
    if config.provider == "square":
        return SquareTerminalProvider(config)
    raise ApiError("terminal_not_configured", "カード端末の設定が不正です。", status=409)
