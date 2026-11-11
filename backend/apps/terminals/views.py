"""
カード決済端末の API。

- 設定（決済会社・認証情報・端末）: 店舗設定の権限＋パスワード再確認
- 会計（端末に送る・結果確認・キャンセル）: 営業操作の権限（PC・iPad どちらの画面からも同じ）
- 決済会社からの通知: 店舗ごとの URL＋署名の検証（ログイン不要）
"""
from __future__ import annotations

import json
import logging

from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.audit import audit
from apps.core.exceptions import ApiError
from apps.core.idempotency import idempotent
from apps.core.models import Store
from apps.core.permissions import CAP_FLOOR, CAP_SETTINGS_EDIT, CAP_SETTINGS_VIEW, StoreCaps

from . import services
from .models import ACTIVE_TERMINAL_STATUSES, CardTerminal, TerminalConfig, TerminalPayment, TerminalPaymentStatus, TerminalWebhookEvent
from .providers import verify_square_signature
from .serializers import CardTerminalSerializer, TerminalConfigSerializer, TerminalPaymentSerializer, effective_mode

log = logging.getLogger("yorumiru.terminals")


def _payment_qs(store):
    return TerminalPayment.objects.filter(store=store).select_related("terminal", "visit__table", "checkout", "requested_by")


def _get_payment(store, tp_id) -> TerminalPayment:
    tp = _payment_qs(store).filter(id=tp_id).first()
    if tp is None:
        raise ApiError("not_found", "端末の決済が見つかりません。", status=404)
    return tp


def _payment_data(store, tp_id):
    return TerminalPaymentSerializer(_get_payment(store, tp_id)).data


def _get_terminal(request, terminal_id) -> CardTerminal:
    t = CardTerminal.objects.filter(store=request.store, id=terminal_id).first()
    if t is None:
        raise ApiError("not_found", "端末が見つかりません。", status=404)
    return t


# --- 設定 ------------------------------------------------------------------------
class TerminalConfigView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_SETTINGS_VIEW, write_caps=(CAP_SETTINGS_EDIT,))]

    def get(self, request):
        return Response(TerminalConfigSerializer(services.config_for(request.store)).data)

    def put(self, request):
        cfg = services.config_for(request.store)
        before = cfg.provider
        ser = TerminalConfigSerializer(cfg, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        for f in ("square_access_token", "square_webhook_signature_key"):
            if f in ser.validated_data and not ser.validated_data[f]:
                ser.validated_data.pop(f)  # 空文字は「変更しない」
        ser.save()
        cfg.refresh_from_db()
        audit(request, "terminal.config.update", target=cfg,
              changes={"provider": [before, cfg.provider], "square_environment": cfg.square_environment, "mode": effective_mode(cfg)})
        return Response(TerminalConfigSerializer(cfg).data)


class TerminalDevicesView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_SETTINGS_VIEW, write_caps=(CAP_SETTINGS_EDIT,))]

    def get(self, request):
        qs = CardTerminal.objects.filter(store=request.store)
        return Response(CardTerminalSerializer(qs, many=True).data)

    def post(self, request):
        t = services.add_terminal(request.store, request.data.get("name", ""))
        audit(request, "terminal.device.add", target=t, changes={"name": t.name, "provider": t.provider})
        return Response(CardTerminalSerializer(t).data, status=201)


class TerminalDeviceDetailView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_SETTINGS_VIEW, write_caps=(CAP_SETTINGS_EDIT,))]

    def patch(self, request, terminal_id):
        t = _get_terminal(request, terminal_id)
        ser = CardTerminalSerializer(t, data={k: v for k, v in request.data.items() if k in ("name", "is_active")}, partial=True)
        ser.is_valid(raise_exception=True)
        ser.save()
        audit(request, "terminal.device.update", target=t, changes=dict(ser.validated_data))
        return Response(CardTerminalSerializer(t).data)


class TerminalPairView(APIView):
    """Square: 端末に入力するペアリングコードを発行（POST）し、つながったか確認（GET）する。"""

    def get_permissions(self):
        return [StoreCaps(CAP_SETTINGS_VIEW, write_caps=(CAP_SETTINGS_EDIT,))]

    def post(self, request, terminal_id):
        t = services.start_pairing(_get_terminal(request, terminal_id))
        audit(request, "terminal.device.pair_start", target=t)
        return Response(CardTerminalSerializer(t).data)

    def get(self, request, terminal_id):
        return Response(CardTerminalSerializer(services.check_pairing(_get_terminal(request, terminal_id))).data)


# --- 会計画面から ---------------------------------------------------------------
class TerminalStatusView(APIView):
    """会計画面用: 端末で払えるか・使える端末・要確認の決済。"""

    def get_permissions(self):
        return [StoreCaps(CAP_FLOOR)]

    def get(self, request):
        cfg = services.config_for(request.store)
        mode = effective_mode(cfg)
        usable = mode in ("mock", "sandbox", "production")
        terminals = list(services.active_terminals(request.store)) if usable else []
        attention = _payment_qs(request.store).filter(status=TerminalPaymentStatus.NEEDS_ATTENTION, resolved_at__isnull=True)[:20]
        return Response({
            "mode": mode,
            "enabled": usable and bool(terminals),
            "is_mock": mode == "mock",
            "terminals": [{"id": t.id, "name": t.name} for t in terminals],
            "needs_attention": TerminalPaymentSerializer(attention, many=True).data,
        })


class VisitTerminalPaymentsView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_FLOOR)]

    def get(self, request, visit_id):
        """この伝票の、処理中または要確認（未対応）の端末決済。会計画面を開き直したときに続きから表示する。"""
        qs = _payment_qs(request.store).filter(visit_id=visit_id)
        active = qs.filter(status__in=ACTIVE_TERMINAL_STATUSES).first()
        attention = qs.filter(status=TerminalPaymentStatus.NEEDS_ATTENTION, resolved_at__isnull=True)
        return Response({
            "active": TerminalPaymentSerializer(active).data if active else None,
            "needs_attention": TerminalPaymentSerializer(attention, many=True).data,
        })

    @idempotent
    def post(self, request, visit_id):
        tp = services.request_payment(request.store, request.user, visit_id, request.data)
        audit(request, "terminal.payment.request", target=tp,
              changes={"visit": tp.visit_id, "amount": tp.amount, "cash_amount": tp.cash_amount, "terminal": tp.terminal.name})
        return Response(_payment_data(request.store, tp.id), status=201)


class TerminalPaymentDetailView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_FLOOR)]

    def get(self, request, tp_id):
        """結果の確認（画面が数秒おきに呼ぶ）。承認されていればこの時点で会計が確定する。"""
        before = _get_payment(request.store, tp_id)
        prev = before.status
        tp = services.refresh(before)
        if tp.status != prev:
            audit(request, "terminal.payment.status", target=tp, changes={"status": [prev, tp.status], "checkout": tp.checkout_id})
        return Response(_payment_data(request.store, tp.id))


class TerminalPaymentCancelView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_FLOOR)]

    def post(self, request, tp_id):
        tp = services.cancel(_get_payment(request.store, tp_id))
        audit(request, "terminal.payment.cancel", target=tp, changes={"status": tp.status})
        return Response(_payment_data(request.store, tp.id))


class TerminalPaymentSimulateView(APIView):
    """模擬端末だけ: 承認／拒否を選ぶ（実際の決済はしない）。"""

    def get_permissions(self):
        return [StoreCaps(CAP_FLOOR)]

    def post(self, request, tp_id):
        approve = request.data.get("result") == "approve"
        tp = services.simulate(_get_payment(request.store, tp_id), approve)
        audit(request, "terminal.payment.simulate", target=tp, changes={"result": "approve" if approve else "decline", "status": tp.status})
        return Response(_payment_data(request.store, tp.id))


class TerminalPaymentResolveView(APIView):
    """「要確認」の決済を、スタッフが対応済みにする（手入力で会計した／端末で返金した など）。"""

    def get_permissions(self):
        return [StoreCaps(CAP_FLOOR)]

    def post(self, request, tp_id):
        note = (request.data.get("note") or "").strip()
        if not note:
            raise ApiError("validation_error", "どう対応したかを入れてください（例: 手入力で会計した、端末で返金した）。")
        with transaction.atomic():
            tp = TerminalPayment.objects.select_for_update().filter(store=request.store, id=tp_id).first()
            if tp is None:
                raise ApiError("not_found", "端末の決済が見つかりません。", status=404)
            if tp.status != TerminalPaymentStatus.NEEDS_ATTENTION or tp.resolved_at:
                raise ApiError("invalid_state", "この決済は対応の必要がありません。", status=409)
            tp.resolved_at = timezone.now()
            tp.resolved_by = request.user
            tp.resolution_note = note[:200]
            tp.save()
        audit(request, "terminal.payment.resolve", target=tp, changes={"note": tp.resolution_note})
        return Response(_payment_data(request.store, tp.id))


# --- 決済会社からの通知 ---------------------------------------------------------
class SquareWebhookView(APIView):
    """
    Square の通知（terminal.checkout.updated）を受け取る。店舗ごとの URL＋署名で確認し、同じ通知は1回だけ処理する。
    通知が届かなくても、会計画面の確認（ポーリング）で結果は取れる。
    """

    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request, token):
        store = Store.objects.filter(webhook_token=token, is_active=True).first()
        if store is None:
            return Response(status=404)
        cfg = TerminalConfig.objects.filter(store=store, provider="square").first()
        if cfg is None or not cfg.square_webhook_signature_key or not cfg.square_webhook_url:
            return Response(status=404)
        body = request.body
        if not verify_square_signature(cfg.square_webhook_signature_key, cfg.square_webhook_url, body,
                                       request.headers.get("X-Square-Hmacsha256-Signature", "")):
            log.warning("square webhook signature invalid store=%s", store.id)
            return Response({"error": {"code": "invalid_signature", "message": "署名が無効です。"}}, status=401)
        try:
            payload = json.loads(body.decode("utf-8"))
        except Exception:
            return Response(status=400)
        event_id = str(payload.get("event_id") or "")[:100]
        event_type = str(payload.get("type") or "")[:60]
        if not event_id:
            return Response({"ok": True})
        try:
            with transaction.atomic():
                TerminalWebhookEvent.objects.create(store=store, event_id=event_id, event_type=event_type, payload=payload)
        except IntegrityError:
            return Response({"ok": True, "duplicate": True})
        if event_type.startswith("terminal.checkout"):
            services.apply_square_webhook(store, payload)
        return Response({"ok": True})
