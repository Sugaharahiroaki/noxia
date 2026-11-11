from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging

from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.api import StoreScopedViewSet, current_store
from apps.core.audit import audit
from apps.core.exceptions import ApiError
from apps.core.idempotency import idempotent
from apps.core.models import Store
from apps.core.permissions import CAP_LINE_MANAGE, CAP_LINE_SEND, CAP_LINE_VIEW, StoreCaps

from .models import Inquiry, LineIntegration, LineUser, MessageDraft, MessageDraftStatus, MessageTemplate, WebhookEvent
from .provider import get_provider
from .serializers import (
    InquirySerializer, LineIntegrationSerializer, LineUserSerializer, MessageDraftSerializer, MessageTemplateSerializer, WebhookEventSerializer,
)

log = logging.getLogger("yorumiru.line")


def _integration(store) -> LineIntegration:
    obj, _ = LineIntegration.objects.get_or_create(store=store)
    return obj


class IntegrationView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_LINE_VIEW, write_caps=(CAP_LINE_MANAGE,), reauth=self.request.method not in ("GET", "HEAD", "OPTIONS"))]

    def get(self, request):
        return Response(LineIntegrationSerializer(_integration(request.store)).data)

    def put(self, request):
        obj = _integration(request.store)
        ser = LineIntegrationSerializer(obj, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        vd = ser.validated_data
        # 空文字はシークレット類を変更しない扱い
        for f in ("channel_secret", "channel_access_token"):
            if f in vd and not vd[f]:
                vd.pop(f)
        ser.save()
        obj.refresh_from_db()
        if obj.has_credentials():
            # 認証情報が揃った時点では「接続（検証前）」とし、疎通確認は別操作。実送信は send_enabled + 環境変数で制御する。
            if obj.status == LineIntegration.STATUS_NOT_CONNECTED:
                obj.status = LineIntegration.STATUS_CONNECTED
        else:
            obj.status = LineIntegration.STATUS_NOT_CONNECTED
        obj.save(update_fields=["status"])
        audit(request, "line.integration.update", target=obj, changes={"status": obj.status, "send_enabled": obj.send_enabled, "webhook_enabled": obj.webhook_enabled})
        return Response(LineIntegrationSerializer(obj).data)


class LineUserViewSet(StoreScopedViewSet):
    model = LineUser
    serializer_class = LineUserSerializer
    caps_read = (CAP_LINE_VIEW,)
    caps_write = (CAP_LINE_MANAGE,)
    http_method_names = ["get", "post", "patch", "head", "options"]

    def get_queryset(self):
        return super().get_queryset().select_related("customer")

    def perform_create(self, serializer):
        # 開発用の手動登録（本来は Webhook の follow イベントで作成される）
        store = current_store(self.request)
        uid = serializer.validated_data.get("line_user_id")
        if LineUser.objects.filter(store=store, line_user_id=uid).exists():
            raise ApiError("validation_error", "この LINE 利用者IDは既に登録されています。")
        serializer.save(store=store)
        audit(self.request, "line.user.create", target=serializer.instance)

    def perform_update(self, serializer):
        obj = serializer.instance
        before_customer = obj.customer_id
        before_consent = obj.consent
        serializer.save()
        if obj.customer_id != before_customer:
            obj.linked_at = timezone.now() if obj.customer_id else None
            obj.linked_by = self.request.user if obj.customer_id else None
            obj.save(update_fields=["linked_at", "linked_by"])
        if obj.consent != before_consent:
            obj.consent_changed_at = timezone.now()
            obj.save(update_fields=["consent_changed_at"])
        audit(self.request, "line.user.update", target=obj, changes={"customer_id": obj.customer_id, "consent": obj.consent})


class TemplateViewSet(StoreScopedViewSet):
    model = MessageTemplate
    serializer_class = MessageTemplateSerializer
    caps_read = (CAP_LINE_VIEW,)
    caps_write = (CAP_LINE_MANAGE,)


class DraftViewSet(StoreScopedViewSet):
    model = MessageDraft
    serializer_class = MessageDraftSerializer
    caps_read = (CAP_LINE_VIEW,)
    caps_write = (CAP_LINE_MANAGE,)
    http_method_names = ["get", "post", "patch", "head", "options"]

    def get_queryset(self):
        return super().get_queryset().select_related("line_user", "template")

    def perform_create(self, serializer):
        store = current_store(self.request)
        body = serializer.validated_data.get("body") or ""
        tpl = serializer.validated_data.get("template")
        lu = serializer.validated_data["line_user"]
        if not body and tpl is not None:
            body = tpl.body
        if not (body or "").strip():
            raise ApiError("validation_error", "本文を入力するか、テンプレートを選択してください。")
        name = lu.customer.display_name if lu.customer_id else (lu.display_name or "お客様")
        body = body.replace("{name}", name).replace("{store}", store.name)
        serializer.save(store=store, body=body, created_by=self.request.user)
        audit(self.request, "line.draft.create", target=serializer.instance)

    def perform_update(self, serializer):
        if serializer.instance.status in (MessageDraftStatus.SENT, MessageDraftStatus.MOCK_SENT):
            raise ApiError("invalid_state", "送信済みの下書きは編集できません。", status=409)
        serializer.save(status=MessageDraftStatus.DRAFT)


class DraftReviewView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_LINE_MANAGE)]

    def post(self, request, draft_id):
        draft = MessageDraft.objects.filter(id=draft_id, store=request.store).first()
        if draft is None:
            raise ApiError("not_found", "下書きが見つかりません。", status=404)
        if draft.status != MessageDraftStatus.DRAFT:
            raise ApiError("invalid_state", "下書き状態のメッセージだけ確認できます。", status=409)
        draft.status = MessageDraftStatus.REVIEWED
        draft.reviewed_by = request.user
        draft.reviewed_at = timezone.now()
        draft.save()
        audit(request, "line.draft.review", target=draft)
        return Response(MessageDraftSerializer(draft).data)


class DraftSendView(APIView):
    """
    送信。送信前確認済み(reviewed)かつ配信同意ありの相手にだけ送る。
    未接続・送信無効のときはモック送信（実送信なし）として記録する。
    """

    def get_permissions(self):
        return [StoreCaps(CAP_LINE_SEND)]

    @idempotent
    @transaction.atomic
    def post(self, request, draft_id):
        draft = MessageDraft.objects.select_for_update().select_related("line_user").filter(id=draft_id, store=request.store).first()
        if draft is None:
            raise ApiError("not_found", "下書きが見つかりません。", status=404)
        if draft.status != MessageDraftStatus.REVIEWED:
            raise ApiError("invalid_state", "送信前確認が済んでいません。", status=409)
        lu = draft.line_user
        if lu.consent != "opted_in" or not lu.is_following:
            draft.status = MessageDraftStatus.BLOCKED
            draft.result = {"ok": False, "detail": "配信同意がない、またはブロック・友だち解除されています。"}
            draft.save()
            audit(request, "line.draft.blocked", target=draft)
            return Response(MessageDraftSerializer(draft).data, status=409)
        integration = _integration(request.store)
        provider = get_provider(integration)
        result = provider.push_text(integration, lu.line_user_id, draft.body)
        draft.result = result.as_dict()
        draft.sent_by = request.user
        draft.sent_at = timezone.now()
        if result.ok:
            draft.status = MessageDraftStatus.MOCK_SENT if result.mock else MessageDraftStatus.SENT
        else:
            draft.status = MessageDraftStatus.FAILED
        draft.save()
        audit(request, "line.draft.send", target=draft, changes={"status": draft.status, "mock": result.mock})
        return Response(MessageDraftSerializer(draft).data)


class InquiryViewSet(StoreScopedViewSet):
    model = Inquiry
    serializer_class = InquirySerializer
    caps_read = (CAP_LINE_VIEW,)
    caps_write = (CAP_LINE_MANAGE,)
    http_method_names = ["get", "patch", "head", "options"]

    def perform_update(self, serializer):
        obj = serializer.save()
        if obj.status == "handled" and obj.handled_at is None:
            obj.handled_at = timezone.now()
            obj.handled_by = self.request.user
            obj.save(update_fields=["handled_at", "handled_by"])


class WebhookEventsView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_LINE_VIEW)]

    def get(self, request):
        return Response(WebhookEventSerializer(WebhookEvent.objects.filter(store=request.store)[:100], many=True).data)


def verify_signature(channel_secret: str, body: bytes, signature: str) -> bool:
    if not channel_secret or not signature:
        return False
    mac = hmac.new(channel_secret.encode("utf-8"), body, hashlib.sha256).digest()
    expected = base64.b64encode(mac).decode("utf-8")
    return hmac.compare_digest(expected, signature)


class WebhookView(APIView):
    """
    LINE Webhook 受信（店舗別パス）。署名を検証し、webhookEventId で重複を排除する。
    認証なし・CSRF 免除（LINE サーバーからの POST）。
    """

    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request, token):
        store = Store.objects.filter(webhook_token=token, is_active=True).select_related("company").first()
        if store is None:
            return Response(status=404)
        integration = LineIntegration.objects.filter(store=store).first()
        if integration is None or not integration.webhook_enabled or not integration.channel_secret:
            return Response(status=404)
        body = request.body
        signature = request.headers.get("X-Line-Signature", "")
        if not verify_signature(integration.channel_secret, body, signature):
            log.warning("LINE webhook signature invalid store=%s", store.id)
            return Response({"error": {"code": "invalid_signature", "message": "署名が無効です。"}}, status=401)
        try:
            payload = json.loads(body.decode("utf-8"))
        except Exception:
            return Response(status=400)
        for ev in payload.get("events") or []:
            self._handle_event(store, ev)
        return Response({"ok": True})

    @transaction.atomic
    def _handle_event(self, store, ev: dict):
        event_id = ev.get("webhookEventId") or ""
        if not event_id:
            return
        try:
            we = WebhookEvent.objects.create(store=store, webhook_event_id=event_id, event_type=ev.get("type", ""), payload=ev)
        except IntegrityError:
            return  # 重複配信（redelivery）: 無視
        source = ev.get("source") or {}
        uid = source.get("userId")
        if not uid:
            we.processed = True
            we.save(update_fields=["processed"])
            return
        lu, _ = LineUser.objects.get_or_create(store=store, line_user_id=uid)
        etype = ev.get("type")
        if etype == "follow":
            lu.is_following = True
            lu.save(update_fields=["is_following"])
        elif etype == "unfollow":
            lu.is_following = False
            lu.consent = "opted_out"
            lu.consent_changed_at = timezone.now()
            lu.save(update_fields=["is_following", "consent", "consent_changed_at"])
        elif etype == "message":
            msg = ev.get("message") or {}
            if msg.get("type") == "text":
                Inquiry.objects.create(store=store, line_user=lu, text=(msg.get("text") or "")[:2000], webhook_event=we)
        we.processed = True
        we.save(update_fields=["processed"])
