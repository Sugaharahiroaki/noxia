"""
領収書の発行記録。

領収書を渡したら、会計ごとに 1 行の記録（ReceiptIssue）を残す。売上・会計履歴の一覧で「発行済み／未発行」を確かめられるようにし、
後から「もらっていない」と言われたときに、いつ・誰が・いくらで出したかを示せるようにする。

- 領収額 = 有効な支払いのうち現金＋カード（未収・ツケは含めない）。金額はここで計算し直さず、会計で確定した支払いをそのまま足す。
- 2 回目からは再発行（reissue=True）。取り消した記録は数えない。
- 記録の取消は行を消さずに cancelled_at を入れる（打ち間違いの訂正用）。
"""
from __future__ import annotations

from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.core.exceptions import ApiError
from apps.core.models import Store
from apps.core.money import ZERO

from .models import Checkout, CheckoutStatus, PaymentMethod, ReceiptIssue, ReceiptIssueMethod

ADDRESSEE_MAX = 60
PROVISO_MAX = 40


def receipt_amount(checkout: Checkout) -> Decimal:
    """領収額（現金＋カード。未収は含めない）。payments は prefetch 済みならそれを使う。"""
    return sum(
        (p.amount for p in checkout.payments.all() if p.status == "valid" and p.method in (PaymentMethod.CASH, PaymentMethod.CARD)),
        ZERO,
    )


def active_issues(checkout: Checkout) -> list[ReceiptIssue]:
    """取り消していない発行記録（古い順）。receipt_issues は prefetch 済みならそれを使う。"""
    return [r for r in checkout.receipt_issues.all() if r.cancelled_at is None]


@transaction.atomic
def record_issue(store: Store, user, checkout_id, *, addressee: str = "", proviso: str = "",
                 method: str = ReceiptIssueMethod.HANDWRITTEN) -> ReceiptIssue:
    """領収書を渡した記録を 1 行足す。取消済みの会計・領収額 0 円（全額ツケ）の会計には付けられない。"""
    checkout = Checkout.objects.select_for_update().filter(id=checkout_id, store=store).first()
    if checkout is None:
        raise ApiError("not_found", "会計が見つかりません。", status=404)
    if checkout.status != CheckoutStatus.CONFIRMED:
        raise ApiError("checkout_voided", "取消済みの会計には領収書を出せません。", status=409)
    if method not in ReceiptIssueMethod.values:
        raise ApiError("validation_error", "発行の方法が正しくありません。")
    amount = receipt_amount(checkout)
    if amount <= 0:
        raise ApiError("validation_error", "領収する金額がありません（全額が後日のお支払いです）。")
    reissue = ReceiptIssue.objects.filter(checkout=checkout, cancelled_at__isnull=True).exists()
    return ReceiptIssue.objects.create(
        store=store, checkout=checkout, method=method, amount=amount,
        addressee=str(addressee or "").strip()[:ADDRESSEE_MAX], proviso=str(proviso or "").strip()[:PROVISO_MAX],
        reissue=reissue, issued_at=timezone.now(), issued_by=user,
    )


@transaction.atomic
def cancel_issue(store: Store, user, issue_id) -> ReceiptIssue:
    """打ち間違いの記録を取り消す（行は残す）。"""
    issue = ReceiptIssue.objects.select_for_update().filter(id=issue_id, store=store).select_related("checkout").first()
    if issue is None:
        raise ApiError("not_found", "領収書の記録が見つかりません。", status=404)
    if issue.cancelled_at is not None:
        raise ApiError("already_cancelled", "この記録はすでに取り消してあります。", status=409)
    issue.cancelled_at = timezone.now()
    issue.cancelled_by = user
    issue.save(update_fields=["cancelled_at", "cancelled_by", "updated_at"])
    return issue
