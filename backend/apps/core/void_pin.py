"""
会計の取消（記録ごと削除）に使う店舗の 4 桁の暗証番号。

- 店舗のタブレットは店舗ごとに 1 アカウントでログインしたまま使うので、取消できる人を暗証番号で絞る。
- 番号はハッシュで保存し、画面にもログにも出さない。設定・変更はオーナー（会社管理者）だけ。
- 5 回続けて間違えると 10 分間止める（4 桁は総当たりされやすいため）。
- 失敗回数は、呼び出し元の処理が失敗しても残るよう、独立したトランザクションで記録する。
"""
from __future__ import annotations

import re
from datetime import timedelta

from django.contrib.auth.hashers import check_password, make_password
from django.db import transaction
from django.utils import timezone

from .exceptions import ApiError
from .models import Store, StoreSettings

MAX_FAILS = 5
LOCK_MINUTES = 10
_PIN_RE = re.compile(r"^\d{4}$")


def is_valid_format(pin) -> bool:
    return isinstance(pin, str) and bool(_PIN_RE.match(pin))


def is_set(store: Store) -> bool:
    return StoreSettings.objects.filter(store=store).exclude(void_pin_hash="").exists()


def set_pin(store: Store, pin: str | None) -> StoreSettings:
    """pin が None / 空なら解除。4 桁の数字以外は受け付けない。"""
    st, _ = StoreSettings.objects.get_or_create(store=store)
    if pin in (None, ""):
        st.void_pin_hash = ""
    else:
        if not is_valid_format(pin):
            raise ApiError("validation_error", "暗証番号は 4 桁の数字で入力してください。", details={"pin": "4 桁の数字"})
        if pin in ("0000", "1234", "1111", "9999"):
            raise ApiError("validation_error", "推測されやすい番号です。別の 4 桁にしてください。", details={"pin": "推測されやすい"})
        st.void_pin_hash = make_password(pin)
    st.void_pin_failed_count = 0
    st.void_pin_locked_until = None
    st.save(update_fields=["void_pin_hash", "void_pin_failed_count", "void_pin_locked_until", "updated_at"])
    return st


def verify(store: Store, pin) -> None:
    """正しければ何も返さない。違えば ApiError（失敗回数・ロックは確定してから送出する）。"""
    now = timezone.now()
    outcome, remaining, locked_until = "ok", 0, None
    with transaction.atomic():
        st = StoreSettings.objects.select_for_update().filter(store=store).first()
        if st is None or not st.void_pin_hash:
            outcome = "not_set"
        elif st.void_pin_locked_until and st.void_pin_locked_until > now:
            outcome, locked_until = "locked", st.void_pin_locked_until
        elif is_valid_format(pin) and check_password(pin, st.void_pin_hash):
            if st.void_pin_failed_count or st.void_pin_locked_until:
                st.void_pin_failed_count = 0
                st.void_pin_locked_until = None
                st.save(update_fields=["void_pin_failed_count", "void_pin_locked_until", "updated_at"])
        else:
            st.void_pin_failed_count += 1
            if st.void_pin_failed_count >= MAX_FAILS:
                st.void_pin_failed_count = 0
                st.void_pin_locked_until = now + timedelta(minutes=LOCK_MINUTES)
                outcome, locked_until = "locked", st.void_pin_locked_until
            else:
                outcome, remaining = "wrong", MAX_FAILS - st.void_pin_failed_count
            st.save(update_fields=["void_pin_failed_count", "void_pin_locked_until", "updated_at"])
    if outcome == "not_set":
        raise ApiError("void_pin_not_set", "会計を取り消すための暗証番号がまだ設定されていません。オーナーがパソコンの「店舗設定」で 4 桁の暗証番号を設定してください。", status=409)
    if outcome == "locked":
        until = timezone.localtime(locked_until).strftime("%H:%M")
        raise ApiError("void_pin_locked", f"暗証番号を {MAX_FAILS} 回間違えたため、{until} まで会計の取消はできません。", status=423,
                       details={"locked_until": locked_until.isoformat()})
    if outcome == "wrong":
        raise ApiError("void_pin_wrong", f"暗証番号が違います（あと {remaining} 回間違えると {LOCK_MINUTES} 分間止まります）。", status=403,
                       details={"remaining": remaining})
