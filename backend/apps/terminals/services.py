"""
決済端末を使った会計の流れ（決済会社に依存しない共通部分）。

  1. request_payment: 会計画面から「端末に送る」。その時点の明細と金額を記録して端末へ送る。
  2. 端末でお客様がカードを読み取る。
  3. refresh（画面からの確認）または決済会社からの通知で結果を受け取る。
  4. 承認されたら finalize: 送った時点の明細で会計を自動で確定（カード払い＋あれば現金）。
     会計の確定は1回だけ（同時に何度呼ばれても二重にならない）。
"""
from __future__ import annotations

import secrets
import uuid

from django.db import transaction
from django.utils import timezone

from apps.cash.services import require_open_business_day
from apps.core.exceptions import ApiError
from apps.core.money import D
from apps.sales import services as sales_services
from apps.sales.models import LineStatus, Visit, VisitStatus
from apps.sales.pricing import compute_totals, line_inputs_from_orderlines

from .models import ACTIVE_TERMINAL_STATUSES, CardTerminal, TerminalConfig, TerminalPayment, TerminalPaymentStatus, TerminalStatus
from .providers import get_provider


def config_for(store) -> TerminalConfig:
    cfg, _ = TerminalConfig.objects.get_or_create(store=store)
    return cfg


def active_terminals(store):
    cfg = config_for(store)
    if cfg.provider == "none":
        return CardTerminal.objects.none()
    return CardTerminal.objects.filter(store=store, provider=cfg.provider, is_active=True, status=TerminalStatus.PAIRED)


@transaction.atomic
def request_payment(store, user, visit_id, data: dict) -> TerminalPayment:
    require_open_business_day(store)
    cfg = config_for(store)
    provider = get_provider(cfg)
    terminal = active_terminals(store).filter(id=data.get("terminal_id")).first()
    if terminal is None:
        raise ApiError("validation_error", "使える決済端末を選んでください（店舗設定で接続済みの端末）。")
    visit = Visit.objects.select_for_update().filter(id=visit_id, store=store).first()
    if visit is None:
        raise ApiError("not_found", "伝票が見つかりません。", status=404)
    if visit.status != VisitStatus.OPEN:
        raise ApiError("visit_closed", "この伝票は会計済みです。", status=409)
    if TerminalPayment.objects.filter(visit=visit, status__in=ACTIVE_TERMINAL_STATUSES).exists():
        raise ApiError("terminal_busy", "この伝票は決済端末で処理中です。端末の画面を確認するか、キャンセルしてから送り直してください。", status=409)

    # 会計する明細（送った時点で固定する）
    open_lines = list(visit.lines.filter(status=LineStatus.ACTIVE, checkout__isnull=True))
    wanted = data.get("line_ids")
    if wanted:
        ids = {int(x) for x in wanted}
        lines = [l for l in open_lines if l.id in ids]
        if len(lines) != len(ids):
            raise ApiError("validation_error", "選んだ明細の一部が既に会計済みか取消済みです。画面を更新してください。", status=409)
    else:
        lines = open_lines
    if not lines:
        raise ApiError("validation_error", "会計する明細がありません。")
    # カード（現金＋カードも）の会計なのでカードのサービス料率。確定（finalize → confirm_checkout）も同じ料率になる
    total = compute_totals(line_inputs_from_orderlines(lines), sales_services.store_settings(store), "card").total

    cash_amount = D(data.get("cash_amount") or 0)
    cash_tendered = D(data["cash_tendered"]) if data.get("cash_tendered") not in (None, "") else None
    if cash_amount < 0:
        raise ApiError("validation_error", "現金の金額が正しくありません。")
    card_amount = total - cash_amount
    if card_amount <= 0:
        raise ApiError("validation_error", "カードで払う金額がありません。")
    if data.get("card_amount") not in (None, "") and D(data["card_amount"]) != card_amount:
        raise ApiError("amount_changed", f"金額が変わりました（いまのカード分は ¥{card_amount:,.0f}）。画面を更新してください。", status=409,
                       details={"card_amount": str(card_amount), "total": str(total)})
    if cash_amount > 0 and (cash_tendered is None or cash_tendered < cash_amount):
        raise ApiError("validation_error", "現金のお預かりが足りません。")

    tp = TerminalPayment.objects.create(
        store=store, visit=visit, terminal=terminal, provider=cfg.provider, amount=card_amount, cash_amount=cash_amount,
        cash_tendered=cash_tendered, line_ids=[l.id for l in lines], requested_by=user,
    )
    result = provider.create_checkout(terminal, card_amount, reference=f"{store.code}-{visit.slip_number}-{tp.id}", idempotency_key=f"noxia-tp-{tp.id}-{uuid.uuid4().hex[:8]}")
    tp.provider_checkout_id = result.checkout_id
    if cfg.provider == "square_pos":
        # Square のアプリから戻ってきたときに、この決済を見分ける値（ログインなしの戻り先で使うので推測できない乱数）
        tp.callback_token = secrets.token_urlsafe(32)
    tp.save(update_fields=["provider_checkout_id", "callback_token", "updated_at"])
    return tp


def square_pos_handoff_url(tp: TerminalPayment) -> str | None:
    """処理中の Square リーダーの決済を、Square のアプリで始める URL（square-commerce-v1://…）。それ以外は None。"""
    if tp.provider != "square_pos" or tp.status != TerminalPaymentStatus.PENDING or not tp.callback_token:
        return None
    from .providers import SquarePosProvider

    provider = SquarePosProvider(config_for(tp.store))
    table = tp.visit.table.name if tp.visit.table_id else ""
    return provider.handoff_url(tp.amount, tp.callback_token, f"NOXIA {table} 伝票#{tp.visit.slip_number}")


def apply_square_pos_result(data: dict) -> TerminalPayment | None:
    """
    Square のアプリからの戻り（?data= の中身）を反映する。state が一致する決済だけ。何度呼ばれても会計は 1 つ。
      成功: status=ok, transaction_id（オフライン決済では無い）, client_transaction_id
      失敗: status=error, error_code（payment_canceled など）
    アクセストークンがあれば Square に問い合わせて、完了と金額を確かめてから会計にする（合わなければ「要確認」）。
    """
    from .providers import SQUARE_POS_ERRORS, SquarePosProvider

    token = str(data.get("state") or "")
    if len(token) < 20:
        return None
    tp = TerminalPayment.objects.select_related("store", "visit").filter(provider="square_pos", callback_token=token).first()
    if tp is None:
        return None
    ok = data.get("status") == "ok"
    txn = str(data.get("transaction_id") or "")[:100]
    client_txn = str(data.get("client_transaction_id") or "")[:100]
    verified, problem = False, ""
    if ok and tp.status == TerminalPaymentStatus.PENDING:
        provider = SquarePosProvider(config_for(tp.store))
        if txn and provider.can_verify():
            verified, problem = provider.verify_order(txn, tp.amount)
    with transaction.atomic():
        locked = TerminalPayment.objects.select_for_update().get(pk=tp.pk)
        if locked.status != TerminalPaymentStatus.PENDING:
            if ok and locked.status in (TerminalPaymentStatus.CANCELED, TerminalPaymentStatus.DECLINED) and not locked.checkout_id:
                # NOXIA で取り消したあとに、Square では決済が終わっていた
                locked.status = TerminalPaymentStatus.NEEDS_ATTENTION
                locked.provider_payment_id = txn or client_txn
                locked.error_message = ("Square では決済済みです（NOXIA で取り消したあとに結果が届きました）。"
                                        "伝票を確認し、二重にならないよう必要なら Square のアプリで返金してください。")[:300]
                locked.save()
            return locked
        if not ok:
            code = str(data.get("error_code") or "")
            detail = SQUARE_POS_ERRORS.get(code, f"Square のアプリで決済できませんでした（{code or '理由不明'}）。")
            _apply_provider_status(locked, "CANCELED" if code == "payment_canceled" else "FAILED", [], detail)
            return locked
        if problem:
            locked.status = TerminalPaymentStatus.NEEDS_ATTENTION
            locked.provider_payment_id = txn
            locked.error_message = f"{problem} 伝票と Square の取引を確認してください。"[:300]
            locked.finished_at = timezone.now()
            locked.save()
            return locked
        locked.provider_verified = verified
        locked.save(update_fields=["provider_verified", "updated_at"])
        _apply_provider_status(locked, "COMPLETED", [txn or client_txn or f"SQPOS-{locked.id}"])
    return finalize(tp.id)


def _apply_provider_status(tp: TerminalPayment, status: str, payment_ids: list[str], detail: str = "") -> None:
    if tp.status != TerminalPaymentStatus.PENDING:
        return
    if status == "COMPLETED":
        tp.status = TerminalPaymentStatus.APPROVED
        tp.provider_payment_id = (payment_ids or [""])[0][:100]
    elif status == "CANCELED":
        tp.status = TerminalPaymentStatus.CANCELED
        tp.error_message = (detail or "端末側でキャンセルされました。")[:300]
        tp.finished_at = timezone.now()
    elif status == "FAILED":
        tp.status = TerminalPaymentStatus.DECLINED
        tp.error_message = (detail or "端末で決済できませんでした。")[:300]
        tp.finished_at = timezone.now()
    tp.save()


def finalize(tp_id: int) -> TerminalPayment:
    """承認済みなら、送った時点の明細で会計を1回だけ確定する。"""
    with transaction.atomic():
        tp = TerminalPayment.objects.select_for_update().select_related("visit", "store", "terminal").get(pk=tp_id)
        if tp.status != TerminalPaymentStatus.APPROVED or tp.checkout_id:
            return tp
        note = f"決済端末（{tp.terminal.name}）"
        if tp.provider == "square_pos" and not tp.provider_verified:
            note += "・Square に照合していない"
        payments = [{"method": "card", "amount": str(tp.amount), "card_reference": (tp.provider_payment_id or f"TERMINAL-{tp.id}")[:60],
                     "note": note}]
        if tp.cash_amount > 0:
            payments.append({"method": "cash", "amount": str(tp.cash_amount), "tendered": str(tp.cash_tendered or tp.cash_amount)})
        try:
            with transaction.atomic():
                checkout = sales_services.confirm_checkout(tp.store, tp.requested_by, tp.visit_id, {"line_ids": tp.line_ids, "payments": payments},
                                                           terminal_payment_id=tp.id)
        except ApiError as e:
            tp.status = TerminalPaymentStatus.NEEDS_ATTENTION
            tp.error_message = (f"端末では決済済みですが、会計を作れませんでした（{e.message}）。"
                                "伝票を確認し、必要なら端末側で返金してください。")[:300]
            tp.finished_at = timezone.now()
            tp.save()
            return tp
        tp.checkout = checkout
        tp.status = TerminalPaymentStatus.COMPLETED
        tp.finished_at = timezone.now()
        tp.save()
        return tp


def refresh(tp: TerminalPayment) -> TerminalPayment:
    """決済会社に結果を問い合わせる（画面から数秒おきに呼ぶ）。承認されていれば会計を確定する。"""
    if tp.status == TerminalPaymentStatus.PENDING:
        provider = get_provider(config_for(tp.store))
        result = provider.get_checkout(tp.provider_checkout_id)
        if result is not None:
            with transaction.atomic():
                locked = TerminalPayment.objects.select_for_update().get(pk=tp.pk)
                _apply_provider_status(locked, result.status, result.payment_ids, result.detail)
    tp.refresh_from_db()
    if tp.status == TerminalPaymentStatus.APPROVED:
        tp = finalize(tp.id)
    return tp


def cancel(tp: TerminalPayment) -> TerminalPayment:
    if tp.status != TerminalPaymentStatus.PENDING:
        raise ApiError("invalid_state", "この決済はもうキャンセルできません。", status=409)
    provider = get_provider(config_for(tp.store))
    result = provider.cancel_checkout(tp.provider_checkout_id)
    with transaction.atomic():
        locked = TerminalPayment.objects.select_for_update().get(pk=tp.pk)
        if result.status == "CANCELED":
            _apply_provider_status(locked, "CANCELED", [], "スタッフがキャンセルしました。")
        else:
            locked.error_message = "キャンセルを端末に送りました。端末の画面を確認してください。"
            locked.save(update_fields=["error_message", "updated_at"])
    tp.refresh_from_db()
    return tp


def simulate(tp: TerminalPayment, approve: bool) -> TerminalPayment:
    """模擬端末だけ: 「承認」「拒否」を画面から決める（実際の決済はしない）。"""
    if tp.provider != "mock":
        raise ApiError("forbidden", "模擬端末のときだけ使えます。", status=403)
    with transaction.atomic():
        locked = TerminalPayment.objects.select_for_update().get(pk=tp.pk)
        if approve:
            _apply_provider_status(locked, "COMPLETED", [f"MOCK-{uuid.uuid4().hex[:10].upper()}"])
        else:
            _apply_provider_status(locked, "FAILED", [], "（模擬）カードが拒否されました。")
    return refresh(TerminalPayment.objects.get(pk=tp.pk))


def apply_square_webhook(store, payload: dict) -> TerminalPayment | None:
    """Square の terminal.checkout.updated 通知を反映する。"""
    obj = ((payload.get("data") or {}).get("object") or {}).get("checkout") or {}
    checkout_id = obj.get("id")
    if not checkout_id:
        return None
    tp = TerminalPayment.objects.filter(store=store, provider="square", provider_checkout_id=checkout_id).first()
    if tp is None:
        return None
    from .providers import SQUARE_STATUS

    with transaction.atomic():
        locked = TerminalPayment.objects.select_for_update().get(pk=tp.pk)
        _apply_provider_status(locked, SQUARE_STATUS.get(obj.get("status", ""), "PENDING"), list(obj.get("payment_ids") or []), obj.get("cancel_reason", "") or "")
    return finalize(tp.id)


# --- 端末の登録・ペアリング --------------------------------------------------------
@transaction.atomic
def add_terminal(store, name: str) -> CardTerminal:
    cfg = config_for(store)
    if cfg.provider == "none":
        raise ApiError("terminal_not_configured", "先に接続する決済会社を選んでください。", status=409)
    name = (name or "").strip()[:40]
    if not name:
        raise ApiError("validation_error", "端末名を入れてください。")
    if CardTerminal.objects.filter(store=store, name=name).exists():
        raise ApiError("validation_error", "同じ名前の端末があります。")
    t = CardTerminal.objects.create(store=store, provider=cfg.provider, name=name)
    if cfg.provider in ("mock", "square_pos"):
        # 模擬端末と Square リーダー（Bluetooth の接続は Square のアプリ側）は、NOXIA でのペアリングが無いのですぐ使える
        t.device_id = f"{cfg.provider}_device_{t.id}"
        t.status = TerminalStatus.PAIRED
        t.paired_at = timezone.now()
        t.save()
    return t


def start_pairing(terminal: CardTerminal) -> CardTerminal:
    provider = get_provider(config_for(terminal.store))
    code_id, code, expires = provider.create_pairing_code(terminal.name, f"noxia-pair-{terminal.id}-{uuid.uuid4().hex[:8]}")
    terminal.pairing_code_id = code_id
    terminal.pairing_code = code[:20]
    terminal.status = TerminalStatus.PAIRING
    terminal.save()
    return terminal


def check_pairing(terminal: CardTerminal) -> CardTerminal:
    if terminal.status != TerminalStatus.PAIRING or not terminal.pairing_code_id:
        return terminal
    device_id = get_provider(config_for(terminal.store)).get_pairing_status(terminal.pairing_code_id)
    if device_id:
        terminal.device_id = device_id
        terminal.status = TerminalStatus.PAIRED
        terminal.paired_at = timezone.now()
        terminal.pairing_code = ""
        terminal.save()
    return terminal
