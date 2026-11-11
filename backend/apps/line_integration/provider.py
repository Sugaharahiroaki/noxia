"""
LINE Messaging API への送信プロバイダ。

- MockProvider: 実際には送らず、送信内容を記録して返す（開発・テスト・未接続時）。
- LineMessagingProvider: Messaging API の push message エンドポイントへ送信する。
  有効化条件: 店舗の接続情報あり + 店舗の send_enabled + 環境変数 LINE_SEND_ENABLED=1。
  いずれかを満たさない場合は MockProvider にフォールバックし、結果に mock=True を明示する。

参考（現行仕様の確認先）: https://developers.line.biz/ja/reference/messaging-api/
  - Push message: POST https://api.line.me/v2/bot/message/push  (Authorization: Bearer {channel access token})
  - Webhook 署名: X-Line-Signature = base64(HMAC-SHA256(channel secret, request body))
  - Webhook イベントには webhookEventId があり、再送（redelivery）時の重複判定に使える。
過去の全トーク履歴や友だち一覧を自由に取得できる API は提供されていないため、
友だち追加（follow）・メッセージ受信（message）など Webhook で届いた情報だけを保持する。
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass

from django.conf import settings

log = logging.getLogger("yorumiru.line")

PUSH_URL = "https://api.line.me/v2/bot/message/push"


@dataclass
class SendResult:
    ok: bool
    mock: bool
    detail: str
    request_id: str | None = None

    def as_dict(self) -> dict:
        return {"ok": self.ok, "mock": self.mock, "detail": self.detail, "request_id": self.request_id}


class MockProvider:
    name = "mock"

    def push_text(self, integration, to_line_user_id: str, text: str) -> SendResult:
        log.info("LINE mock send to=%s len=%d", to_line_user_id[:6] + "***", len(text))
        return SendResult(ok=True, mock=True, detail="モック送信（実際には送信していません）")


class LineMessagingProvider:
    name = "line"

    def push_text(self, integration, to_line_user_id: str, text: str) -> SendResult:
        body = json.dumps({"to": to_line_user_id, "messages": [{"type": "text", "text": text[:5000]}]}).encode("utf-8")
        req = urllib.request.Request(
            PUSH_URL, data=body, method="POST",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {integration.channel_access_token}"},
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                rid = resp.headers.get("x-line-request-id")
                return SendResult(ok=True, mock=False, detail="送信しました", request_id=rid)
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            log.warning("LINE push failed status=%s", e.code)
            return SendResult(ok=False, mock=False, detail=f"LINE API エラー ({e.code}): {detail}")
        except Exception as e:  # ネットワーク等
            log.warning("LINE push error: %s", type(e).__name__)
            return SendResult(ok=False, mock=False, detail=f"送信に失敗しました: {type(e).__name__}")


def get_provider(integration):
    """送信可能な条件を満たすときだけ実プロバイダを返す。"""
    if (
        integration is not None
        and integration.has_credentials()
        and integration.status == integration.STATUS_CONNECTED
        and integration.send_enabled
        and settings.LINE_SEND_ENABLED
    ):
        return LineMessagingProvider()
    return MockProvider()
