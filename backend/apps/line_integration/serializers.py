from __future__ import annotations

from rest_framework import serializers

from apps.core.api import StorePK
from apps.customers.models import Customer, Reservation

from .models import Inquiry, LineIntegration, LineUser, MessageDraft, MessageTemplate, WebhookEvent


def _mask(value: str, keep: int = 4) -> str:
    if not value:
        return ""
    return "*" * 6 + value[-keep:]


class LineIntegrationSerializer(serializers.ModelSerializer):
    """シークレット・トークンは末尾だけ返す。書き込みは専用フィールドで受ける。"""

    channel_secret_masked = serializers.SerializerMethodField()
    channel_access_token_masked = serializers.SerializerMethodField()
    has_credentials = serializers.SerializerMethodField()
    channel_secret = serializers.CharField(write_only=True, required=False, allow_blank=True)
    channel_access_token = serializers.CharField(write_only=True, required=False, allow_blank=True)
    webhook_path = serializers.SerializerMethodField()
    effective_send_mode = serializers.SerializerMethodField()

    class Meta:
        model = LineIntegration
        fields = ["channel_id", "channel_secret", "channel_access_token", "channel_secret_masked", "channel_access_token_masked", "has_credentials",
                  "status", "webhook_enabled", "send_enabled", "official_account_name", "last_verified_at", "note", "webhook_path", "effective_send_mode"]
        read_only_fields = ["status", "last_verified_at"]

    def get_channel_secret_masked(self, obj):
        return _mask(obj.channel_secret)

    def get_channel_access_token_masked(self, obj):
        return _mask(obj.channel_access_token)

    def get_has_credentials(self, obj):
        return obj.has_credentials()

    def get_webhook_path(self, obj):
        token = obj.store.webhook_token
        return f"/api/line/webhook/{token}" if token else None

    def get_effective_send_mode(self, obj):
        from django.conf import settings

        if obj.has_credentials() and obj.status == obj.STATUS_CONNECTED and obj.send_enabled and settings.LINE_SEND_ENABLED:
            return "live"
        return "mock"


class LineUserSerializer(serializers.ModelSerializer):
    customer = StorePK(model=Customer, scope="company", required=False, allow_null=True)
    customer_name = serializers.CharField(source="customer.display_name", read_only=True, default=None)
    consent_label = serializers.CharField(source="get_consent_display", read_only=True)

    class Meta:
        model = LineUser
        fields = ["id", "line_user_id", "display_name", "consent", "consent_label", "consent_changed_at", "customer", "customer_name", "linked_at",
                  "is_following", "note", "created_at"]
        read_only_fields = ["id", "linked_at", "created_at"]

    def validate_line_user_id(self, value):
        # 更新時は変更不可（Webhook で得た ID を書き換えない）
        if self.instance is not None and value != self.instance.line_user_id:
            raise serializers.ValidationError("LINE 利用者IDは変更できません。")
        return value


class MessageTemplateSerializer(serializers.ModelSerializer):
    class Meta:
        model = MessageTemplate
        fields = ["id", "name", "kind", "body", "is_active"]
        read_only_fields = ["id"]


class MessageDraftSerializer(serializers.ModelSerializer):
    line_user = StorePK(model=LineUser)
    template = StorePK(model=MessageTemplate, required=False, allow_null=True)
    reservation = StorePK(model=Reservation, required=False, allow_null=True)
    inquiry = StorePK(model=Inquiry, required=False, allow_null=True)
    line_user_display = serializers.CharField(source="line_user.display_name", read_only=True)
    line_user_consent = serializers.CharField(source="line_user.consent", read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    # テンプレート指定時は本文を省略できる（テンプレート本文を展開する）
    body = serializers.CharField(required=False, allow_blank=True)

    class Meta:
        model = MessageDraft
        fields = ["id", "line_user", "line_user_display", "line_user_consent", "template", "body", "status", "status_label", "reservation", "inquiry",
                  "reviewed_at", "sent_at", "result", "created_at"]
        read_only_fields = ["id", "status", "reviewed_at", "sent_at", "result", "created_at"]


class InquirySerializer(serializers.ModelSerializer):
    line_user_display = serializers.CharField(source="line_user.display_name", read_only=True)
    reservation = StorePK(model=Reservation, required=False, allow_null=True)

    class Meta:
        model = Inquiry
        fields = ["id", "line_user", "line_user_display", "text", "status", "handled_at", "reservation", "created_at"]
        read_only_fields = ["id", "line_user", "text", "handled_at", "created_at"]


class WebhookEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = WebhookEvent
        fields = ["id", "webhook_event_id", "event_type", "received_at", "processed", "signature_valid"]
