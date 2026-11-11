"""
AI 読取の接続部品（terminals/providers.py・accounting/freee.py と同じ方式）。

- mock: 固定結果（開発・テスト。ネットワークなし。sha256 から決定的に作る）
- anthropic: Claude の画像・PDF 直読み（構造化出力）。settings.RECEIPT_AI_PROVIDER == "anthropic" かつ ANTHROPIC_API_KEY かつ会社設定 ai_enabled のときだけ。
- 送るのは縮小・再エンコード済みの画像（または PDF）だけ。ファイル名・店舗名・個人名は送らない。
- 安いモデルで読み、心もとないときだけ高精度モデルへ回す（2段構え）。
"""
from __future__ import annotations

import base64
import hashlib
import json
from datetime import date, timedelta
from decimal import Decimal

from django.conf import settings

from .schema import PROMPT_VERSION, RECEIPT_SCHEMA, SYSTEM_PROMPT, OcrResult, validate_result


class OcrError(Exception):
    def __init__(self, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


class BaseProvider:
    name = "base"

    def read(self, data: bytes, content_type: str, *, accounts: list[dict], today: date | None = None, sha256: str = "") -> OcrResult:  # pragma: no cover
        raise NotImplementedError


# テスト用: sha256 の先頭16桁 → dict（結果の上書き）か Exception
MOCK_OVERRIDES: dict[str, object] = {}
_MOCK_VENDORS = ["○○マート", "酒のやまや", "業務スーパー", "ドン・キホーテ", "セブン-イレブン", "ローソン", "氷屋 山田商店"]


class MockProvider(BaseProvider):
    """ネットワークを使わない決定的な読取。画像のハッシュから金額などを作る。"""

    name = "mock"

    def read(self, data: bytes, content_type: str, *, accounts: list[dict], today: date | None = None, sha256: str = "") -> OcrResult:
        digest = sha256 or hashlib.sha256(data).hexdigest()
        override = MOCK_OVERRIDES.get(digest[:16])
        if isinstance(override, Exception):
            raise override
        today = today or date.today()
        n = int(digest[:6], 16)
        total = Decimal(1000 + (n % 90) * 100)  # 1,000〜9,900 円
        vendor = _MOCK_VENDORS[n % len(_MOCK_VENDORS)]
        keys = {a["key"] for a in accounts}
        suggested = [k for k in ("supplies", "purchases", "misc_expense") if k in keys][:2]
        payload = {
            "is_receipt": True, "receipt_count": 1, "date": (today - timedelta(days=1)).isoformat(), "vendor": vendor,
            "total_amount": str(total), "tax_amount": str((total * 10 / 110).quantize(Decimal(1))),
            "tax_breakdown": [{"rate": "10", "taxable_incl": str(total), "tax": str((total * 10 / 110).quantize(Decimal(1)))}],
            "invoice_registration_number": None, "payment_method": "cash", "summary": "消耗品",
            "suggested_accounts": [{"key": k, "reason": "模擬読取"} for k in suggested], "missing_fields": [], "warnings": [],
        }
        if isinstance(override, dict):
            payload.update(override)
        r = validate_result(payload, account_keys=keys, provider=self.name, model="mock")
        r.cost_jpy = Decimal("0")
        return r


# Anthropic の料金目安（円/百万トークン・2026-11 時点の概算。原価の把握用で、正確な請求額ではない）
_PRICE_JPY_PER_MTOK = {"default": (450, 2250), "claude-opus-5-5": (2250, 11250), "claude-sonnet-5-5": (450, 2250), "claude-haiku-5-5": (150, 750)}


class AnthropicProvider(BaseProvider):
    name = "anthropic"

    def __init__(self, api_key: str, model: str, fallback_model: str = "", timeout: int = 120):
        self.api_key, self.model, self.fallback_model, self.timeout = api_key, model, fallback_model, timeout

    def _client(self):
        try:
            import anthropic
        except ImportError as e:  # pragma: no cover
            raise OcrError("anthropic パッケージがありません") from e
        return anthropic.Anthropic(api_key=self.api_key, max_retries=0, timeout=self.timeout)

    def _call(self, client, model: str, data: bytes, content_type: str, accounts: list[dict], today: date) -> tuple[dict, dict]:
        import anthropic

        b64 = base64.b64encode(data).decode("ascii")
        if content_type == "application/pdf":
            block = {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": b64}}
        else:
            block = {"type": "image", "source": {"type": "base64", "media_type": content_type if content_type in ("image/jpeg", "image/png", "image/webp") else "image/jpeg", "data": b64}}
        account_list = "\n".join(f"- {a['key']}: {a['name']}" for a in accounts)
        user_text = f"本日は {today.isoformat()} です。勘定科目一覧（key: 名前）:\n{account_list}\n\nこの画像から指定スキーマの JSON だけを返してください。"
        tool = {"name": "receipt_result", "description": "領収書の読取結果", "input_schema": RECEIPT_SCHEMA}
        try:
            msg = client.messages.create(
                model=model, max_tokens=2048, system=SYSTEM_PROMPT, tools=[tool], tool_choice={"type": "tool", "name": "receipt_result"},
                messages=[{"role": "user", "content": [block, {"type": "text", "text": user_text}]}],
            )
        except anthropic.RateLimitError as e:
            raise OcrError(f"AI の利用上限（レート制限）: {e}", retryable=True) from e
        except anthropic.APIConnectionError as e:
            raise OcrError(f"AI に接続できません: {e}", retryable=True) from e
        except anthropic.InternalServerError as e:
            raise OcrError(f"AI 側のエラー: {e}", retryable=True) from e
        except anthropic.APIStatusError as e:
            raise OcrError(f"AI のエラー ({e.status_code})", retryable=False) from e
        if getattr(msg, "stop_reason", "") == "refusal":
            raise OcrError("AI が読取を拒否しました。原本を確認して手で入力してください。")
        for c in msg.content:
            if getattr(c, "type", "") == "tool_use":
                usage = {"input_tokens": getattr(msg.usage, "input_tokens", 0), "output_tokens": getattr(msg.usage, "output_tokens", 0), "model": model}
                return (c.input if isinstance(c.input, dict) else json.loads(json.dumps(c.input))), usage
        raise OcrError("AI の応答に読取結果がありません。原本を確認して手で入力してください。")

    def _cost(self, usage: dict) -> Decimal:
        pin, pout = _PRICE_JPY_PER_MTOK.get(usage.get("model", ""), _PRICE_JPY_PER_MTOK["default"])
        return (Decimal(usage.get("input_tokens", 0)) * pin + Decimal(usage.get("output_tokens", 0)) * pout) / Decimal(1_000_000)

    def read(self, data: bytes, content_type: str, *, accounts: list[dict], today: date | None = None, sha256: str = "") -> OcrResult:
        today = today or date.today()
        keys = {a["key"] for a in accounts}
        client = self._client()
        payload, usage = self._call(client, self.model, data, content_type, accounts, today)
        r = validate_result(payload, account_keys=keys, provider=self.name, model=self.model)
        r.cost_jpy = self._cost(usage).quantize(Decimal("0.01"))
        r.raw["_usage"] = usage
        if r.needs_retry and self.fallback_model and self.fallback_model != self.model:
            try:
                payload2, usage2 = self._call(client, self.fallback_model, data, content_type, accounts, today)
            except OcrError:
                r.warnings.append({"code": "fallback_failed", "message": "高精度モデルでの読み直しに失敗しました"})
                return r
            r2 = validate_result(payload2, account_keys=keys, provider=self.name, model=self.fallback_model)
            r2.cost_jpy = (r.cost_jpy + self._cost(usage2)).quantize(Decimal("0.01"))
            r2.raw["_usage"] = usage2
            r2.raw["_first_pass"] = {"model": self.model, "warnings": r.warnings}
            return r2 if not r2.needs_retry or r.total is None else r
        return r


def server_provider_name() -> str:
    return (getattr(settings, "RECEIPT_AI_PROVIDER", "mock") or "none").strip().lower()


def get_provider(company) -> BaseProvider | None:
    """会社設定と環境変数の両方がそろったときだけ接続部品を返す。無ければ None（手入力）。"""
    from apps.core.models import CompanySettings

    cs = CompanySettings.objects.filter(company=company).first()
    if cs is None or not cs.ai_enabled:
        return None
    name = (cs.ai_provider or server_provider_name()).lower()
    if name in ("", "none", "off"):
        return None
    if name == "mock":
        return MockProvider()
    if name == "anthropic":
        key = getattr(settings, "ANTHROPIC_API_KEY", "")
        if not key or server_provider_name() != "anthropic":
            return None
        return AnthropicProvider(key, getattr(settings, "RECEIPT_AI_MODEL", "claude-sonnet-5-5"), getattr(settings, "RECEIPT_AI_FALLBACK_MODEL", ""),
                                 int(getattr(settings, "RECEIPT_AI_TIMEOUT", 120)))
    return None


__all__ = ["BaseProvider", "MockProvider", "AnthropicProvider", "OcrError", "OcrResult", "get_provider", "server_provider_name", "MOCK_OVERRIDES", "PROMPT_VERSION"]
