from __future__ import annotations

from django.conf import settings
from rest_framework import serializers

from apps.core.exceptions import ApiError

from .models import CardTerminal, TerminalConfig, TerminalPayment, TerminalProviderKind


def _mask(value: str, keep: int = 4) -> str:
    if not value:
        return ""
    return "*" * 6 + value[-keep:]


def effective_mode(cfg: TerminalConfig) -> str:
    """画面に出す「いまの動き」: off / mock / mock_disabled / sandbox / production / production_blocked / incomplete"""
    if cfg.provider == TerminalProviderKind.NONE:
        return "off"
    if cfg.provider == TerminalProviderKind.MOCK:
        return "mock" if settings.CARD_TERMINAL_MOCK_ENABLED else "mock_disabled"
    if not cfg.square_access_token or not cfg.square_location_id:
        return "incomplete"
    if cfg.square_environment == "production":
        return "production" if settings.CARD_TERMINAL_LIVE_ENABLED else "production_blocked"
    return "sandbox"


class TerminalConfigSerializer(serializers.ModelSerializer):
    """アクセストークン・署名キーは末尾だけ返す。空文字で送られたら変更しない。"""

    square_access_token = serializers.CharField(write_only=True, required=False, allow_blank=True)
    square_webhook_signature_key = serializers.CharField(write_only=True, required=False, allow_blank=True)
    square_access_token_masked = serializers.SerializerMethodField()
    square_webhook_signature_key_masked = serializers.SerializerMethodField()
    webhook_path = serializers.SerializerMethodField()
    mode = serializers.SerializerMethodField()
    live_enabled = serializers.SerializerMethodField()
    mock_allowed = serializers.SerializerMethodField()

    class Meta:
        model = TerminalConfig
        fields = ["provider", "square_environment", "square_location_id", "square_access_token", "square_webhook_signature_key",
                  "square_access_token_masked", "square_webhook_signature_key_masked", "square_webhook_url", "note",
                  "webhook_path", "mode", "live_enabled", "mock_allowed", "updated_at"]
        read_only_fields = ["updated_at"]

    def get_square_access_token_masked(self, obj):
        return _mask(obj.square_access_token)

    def get_square_webhook_signature_key_masked(self, obj):
        return _mask(obj.square_webhook_signature_key)

    def get_webhook_path(self, obj):
        token = obj.store.webhook_token
        return f"/api/terminals/webhook/{token}" if token else None

    def get_mode(self, obj):
        return effective_mode(obj)

    def get_live_enabled(self, obj):
        return bool(settings.CARD_TERMINAL_LIVE_ENABLED)

    def get_mock_allowed(self, obj):
        return bool(settings.CARD_TERMINAL_MOCK_ENABLED)

    def validate_square_environment(self, value):
        if value not in ("sandbox", "production"):
            raise serializers.ValidationError("sandbox か production を選んでください。")
        return value

    def validate_provider(self, value):
        if value == TerminalProviderKind.MOCK and not settings.CARD_TERMINAL_MOCK_ENABLED:
            raise ApiError("terminal_mock_disabled", "模擬端末はこのサーバーでは使えません（CARD_TERMINAL_MOCK_ENABLED）。", status=409)
        return value

    def validate_square_webhook_url(self, value):
        value = (value or "").strip()
        if value and not value.startswith("https://"):
            raise serializers.ValidationError("https:// で始まる URL を入れてください（Square に登録したものと同じ）。")
        return value


class CardTerminalSerializer(serializers.ModelSerializer):
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    provider_label = serializers.CharField(source="get_provider_display", read_only=True)

    class Meta:
        model = CardTerminal
        fields = ["id", "name", "provider", "provider_label", "device_id", "status", "status_label", "pairing_code",
                  "pairing_expires_at", "paired_at", "is_active"]
        read_only_fields = ["provider", "device_id", "status", "pairing_code", "pairing_expires_at", "paired_at"]


class TerminalPaymentSerializer(serializers.ModelSerializer):
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    terminal_name = serializers.CharField(source="terminal.name", read_only=True)
    is_mock = serializers.SerializerMethodField()
    change_amount = serializers.SerializerMethodField()
    checkout_number = serializers.SerializerMethodField()
    slip_number = serializers.IntegerField(source="visit.slip_number", read_only=True)
    table_name = serializers.SerializerMethodField()
    requested_by_name = serializers.SerializerMethodField()

    class Meta:
        model = TerminalPayment
        fields = ["id", "visit", "slip_number", "table_name", "terminal", "terminal_name", "provider", "is_mock", "amount", "cash_amount",
                  "cash_tendered", "change_amount", "line_ids", "status", "status_label", "error_message", "provider_payment_id",
                  "checkout", "checkout_number", "requested_by_name", "created_at", "finished_at", "resolved_at", "resolution_note"]
        read_only_fields = fields

    def get_is_mock(self, obj):
        return obj.provider == TerminalProviderKind.MOCK

    def get_change_amount(self, obj):
        if obj.cash_amount and obj.cash_tendered is not None:
            return str(obj.cash_tendered - obj.cash_amount)
        return "0"

    def get_checkout_number(self, obj):
        return obj.checkout.number if obj.checkout_id else None

    def get_table_name(self, obj):
        return obj.visit.table.name if obj.visit.table_id else ""

    def get_requested_by_name(self, obj):
        u = obj.requested_by
        if u is None:
            return ""
        return getattr(u, "display_name", "") or u.get_username()
