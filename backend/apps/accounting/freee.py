"""
freee 会計 API との接続部品と、仕訳の送信（振替伝票 /api/1/manual_journals）。

確認した仕様（2026-11 時点、developer.freee.co.jp）:
- 認証は OAuth 2.0 認可コード方式。アクセストークンの有効期限は6時間、リフレッシュトークンは90日で、
  1つのリフレッシュトークンで2回は更新できない（更新のたびに新しいリフレッシュトークンを保存する）。
- 振替伝票の作成 POST /api/1/manual_journals のレスポンスには 2026-07-01 以降 txn_number が含まれない（id を保存する）。

送信の安全策（二重登録の防止）:
- 仕訳ごとに FreeeSync を1つだけ持ち、freee の振替伝票 ID を保存する。ID があれば作成ではなく更新・削除にする。
- 送信前に状態を「送信中」にして保存してから freee に送る。送信中のまま残った（通信が途中で切れた）ものは
  「結果不明」にして自動では再送しない（freee 側に作られている可能性があるため。画面で確認してから再送する）。
- 摘要に [NOXIA:仕訳ID] を入れ、freee 側で NOXIA から来た仕訳を探せるようにする。

本番の freee への送信は FREEE_LIVE_ENABLED=1 かつ FREEE_CLIENT_ID / FREEE_CLIENT_SECRET / FREEE_REDIRECT_URI があるときだけ。
それ以外は模擬接続（MockFreeeClient: 送った内容を記録するだけ）で、開発・テスト・操作練習に使う。
"""
from __future__ import annotations

import json
import logging
import secrets
import urllib.error
import urllib.parse
import urllib.request
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.core.exceptions import ApiError
from apps.core.money import D

from .journals import TAXES, account_names
from .models import AccountMapping, FreeeConnection, FreeeSync, FreeeSyncLog, FreeeSyncStatus, Journal

log = logging.getLogger("yorumiru.freee")

AUTHORIZE_URL = "https://accounts.secure.freee.co.jp/public_api/authorize"
TOKEN_URL = "https://accounts.secure.freee.co.jp/public_api/token"
API_BASE = "https://api.freee.co.jp"


class FreeeError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def live_enabled() -> bool:
    return bool(getattr(settings, "FREEE_LIVE_ENABLED", False) and settings.FREEE_CLIENT_ID and settings.FREEE_CLIENT_SECRET and settings.FREEE_REDIRECT_URI)


# --- 接続部品 ---------------------------------------------------------------------
class MockFreeeClient:
    """模擬接続。freee には送らず、IDを払い出して記録するだけ。"""

    is_mock = True
    calls: list[dict] = []  # テストで送信内容を確かめる

    def __init__(self, conn: FreeeConnection):
        self.conn = conn

    def create_manual_journal(self, body: dict) -> int:
        MockFreeeClient.calls.append({"action": "create", "body": body})
        return 900000 + len(MockFreeeClient.calls)

    def update_manual_journal(self, freee_id: int, body: dict) -> None:
        MockFreeeClient.calls.append({"action": "update", "id": freee_id, "body": body})

    def delete_manual_journal(self, freee_id: int) -> None:
        MockFreeeClient.calls.append({"action": "delete", "id": freee_id})

    def account_items(self) -> list[dict]:
        return [{"id": 100 + i, "name": n} for i, n in enumerate(sorted({v[0] for v in _account_defaults()} | {v[1] for v in _account_defaults()}))]

    def taxes(self) -> list[dict]:
        return [{"code": 100 + i, "name_ja": n} for i, n in enumerate(TAXES.values())]


def _account_defaults():
    from .journals import ACCOUNTS

    return ACCOUNTS.values()


class LiveFreeeClient:
    is_mock = False

    def __init__(self, conn: FreeeConnection):
        self.conn = conn

    def _token(self) -> str:
        c = self.conn
        if not c.access_token:
            raise FreeeError("freee と接続されていません。")
        if c.token_expires_at and c.token_expires_at <= timezone.now() + timedelta(minutes=5):
            refresh_token(c)
        return c.access_token

    def _request(self, method: str, path: str, body: dict | None = None, query: dict | None = None) -> dict:
        url = API_BASE + path + ("?" + urllib.parse.urlencode(query) if query else "")
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers={
            "Authorization": f"Bearer {self._token()}", "Content-Type": "application/json", "Accept": "application/json",
        })
        try:
            with urllib.request.urlopen(req, timeout=20) as res:
                text = res.read().decode()
                return json.loads(text) if text else {}
        except urllib.error.HTTPError as e:
            msg = e.read().decode(errors="replace")[:300]
            raise FreeeError(f"freee API エラー ({e.code}): {msg}", status=e.code)
        except urllib.error.URLError as e:
            raise FreeeError(f"freee に接続できません: {e.reason}")

    def create_manual_journal(self, body: dict) -> int:
        res = self._request("POST", "/api/1/manual_journals", body)
        return int(res["manual_journal"]["id"])

    def update_manual_journal(self, freee_id: int, body: dict) -> None:
        self._request("PUT", f"/api/1/manual_journals/{freee_id}", body)

    def delete_manual_journal(self, freee_id: int) -> None:
        self._request("DELETE", f"/api/1/manual_journals/{freee_id}", query={"company_id": self.conn.freee_company_id})

    def account_items(self) -> list[dict]:
        res = self._request("GET", "/api/1/account_items", query={"company_id": self.conn.freee_company_id})
        return [{"id": a["id"], "name": a["name"]} for a in res.get("account_items", [])]

    def taxes(self) -> list[dict]:
        res = self._request("GET", f"/api/1/taxes/companies/{self.conn.freee_company_id}")
        return [{"code": t["code"], "name_ja": t.get("name_ja") or t.get("name")} for t in res.get("taxes", [])]

    def companies(self) -> list[dict]:
        res = self._request("GET", "/api/1/companies")
        return [{"id": c["id"], "name": c.get("display_name") or c.get("name")} for c in res.get("companies", [])]


def client_for(conn: FreeeConnection):
    if conn.mode == "live":
        if not live_enabled():
            raise ApiError("freee_live_disabled", "freee 本番への送信はサーバー設定で止められています（FREEE_LIVE_ENABLED）。", status=409)
        return LiveFreeeClient(conn)
    return MockFreeeClient(conn)


# --- OAuth -------------------------------------------------------------------------
def authorize_url(request, taxpayer_id: int) -> str:
    state = secrets.token_urlsafe(24)
    request.session["freee_oauth_state"] = {"state": state, "taxpayer_id": taxpayer_id}
    q = {"response_type": "code", "client_id": settings.FREEE_CLIENT_ID, "redirect_uri": settings.FREEE_REDIRECT_URI, "state": state, "prompt": "select_company"}
    return AUTHORIZE_URL + "?" + urllib.parse.urlencode(q)


def _token_request(params: dict) -> dict:
    data = urllib.parse.urlencode({**params, "client_id": settings.FREEE_CLIENT_ID, "client_secret": settings.FREEE_CLIENT_SECRET}).encode()
    req = urllib.request.Request(TOKEN_URL, data=data, method="POST", headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=20) as res:
            return json.loads(res.read().decode())
    except urllib.error.HTTPError as e:
        raise FreeeError(f"freee の認証に失敗しました ({e.code}): {e.read().decode(errors='replace')[:200]}", status=e.code)
    except urllib.error.URLError as e:
        raise FreeeError(f"freee に接続できません: {e.reason}")


def _save_tokens(conn: FreeeConnection, tok: dict) -> None:
    conn.access_token = tok["access_token"]
    conn.refresh_token = tok.get("refresh_token") or conn.refresh_token
    conn.token_expires_at = timezone.now() + timedelta(seconds=int(tok.get("expires_in") or 21600))
    if tok.get("company_id"):
        conn.freee_company_id = int(tok["company_id"])


def exchange_code(conn: FreeeConnection, code: str) -> None:
    tok = _token_request({"grant_type": "authorization_code", "code": code, "redirect_uri": settings.FREEE_REDIRECT_URI})
    _save_tokens(conn, tok)


def refresh_token(conn: FreeeConnection) -> None:
    """リフレッシュトークンは1回しか使えないので、行ロックしてから更新し、新しいトークンをすぐ保存する。"""
    with transaction.atomic():
        locked = FreeeConnection.objects.select_for_update().get(pk=conn.pk)
        if locked.token_expires_at and locked.token_expires_at > timezone.now() + timedelta(minutes=5):
            conn.access_token, conn.refresh_token, conn.token_expires_at = locked.access_token, locked.refresh_token, locked.token_expires_at
            return  # 別の処理が更新済み
        tok = _token_request({"grant_type": "refresh_token", "refresh_token": locked.refresh_token})
        _save_tokens(locked, tok)
        locked.save(update_fields=["access_token", "refresh_token", "token_expires_at", "freee_company_id", "updated_at"])
        conn.access_token, conn.refresh_token, conn.token_expires_at = locked.access_token, locked.refresh_token, locked.token_expires_at


# --- 送信 ---------------------------------------------------------------------------
def manual_journal_body(journal: Journal, conn: FreeeConnection, *, require_ids: bool) -> dict:
    mappings = {m.key: m for m in AccountMapping.objects.filter(taxpayer=journal.taxpayer)}
    names = account_names(journal.taxpayer)
    details = []
    missing = set()
    for ln in journal.lines:
        acc = mappings.get(ln["key"])
        tax = mappings.get(f"tax:{ln['tax']}")
        acc_id = acc.freee_account_item_id if acc else None
        tax_code = tax.tax_code if tax else None
        if require_ids and (acc_id is None or tax_code is None):
            missing.add(names.get(ln["key"], ln["key"]) if acc_id is None else TAXES.get(ln["tax"], ln["tax"]))
        details.append({
            "entry_side": ln["side"],
            "account_item_id": acc_id,
            "tax_code": tax_code,
            "amount": int(D(ln["amount"])),
            "description": f"{ln.get('memo') or journal.description} [NOXIA:{journal.id}]"[:250],
        })
    if missing:
        raise FreeeError("freee の勘定科目・税区分の対応付けが未設定です: " + "、".join(sorted(missing)))
    return {"company_id": conn.freee_company_id, "issue_date": journal.issue_date.isoformat(), "adjustment": False, "details": details}


def _log(sync: FreeeSync, action: str, ok: bool, message: str = "", status: int | None = None, user=None):
    FreeeSyncLog.objects.create(sync=sync, action=action, ok=ok, http_status=status, message=message[:500], created_by=user)


SENDABLE = (FreeeSyncStatus.PENDING, FreeeSyncStatus.FAILED, FreeeSyncStatus.NEEDS_UPDATE, FreeeSyncStatus.NEEDS_DELETE)


def send_one(sync_id: int, conn: FreeeConnection, user=None) -> FreeeSync:
    """仕訳1件を freee に送る（作成・更新・削除）。状態の変更は送信の前後でそれぞれ確定させる。"""
    with transaction.atomic():
        sync = FreeeSync.objects.select_for_update().select_related("journal__taxpayer").get(pk=sync_id)
        if sync.status not in SENDABLE:
            return sync
        if conn.sync_from and sync.journal.issue_date < conn.sync_from:
            return sync
        journal = sync.journal
        if journal.is_void and not sync.freee_id:
            sync.status = FreeeSyncStatus.DELETED
            sync.save(update_fields=["status", "updated_at"])
            return sync
        if not journal.is_void and sync.freee_id and sync.synced_hash == journal.content_hash:
            sync.status = FreeeSyncStatus.SYNCED  # 同じ内容は送り直さない
            sync.save(update_fields=["status", "updated_at"])
            return sync
        action = "delete" if journal.is_void else ("update" if sync.freee_id else "create")
        sync.status = FreeeSyncStatus.SENDING
        sync.attempts += 1
        sync.last_attempt_at = timezone.now()
        sync.save(update_fields=["status", "attempts", "last_attempt_at", "updated_at"])
        sending_hash = journal.content_hash
    client = client_for(conn)
    try:
        body = None if action == "delete" else manual_journal_body(journal, conn, require_ids=not client.is_mock)
        if action == "create":
            new_id = client.create_manual_journal(body)
        elif action == "update":
            client.update_manual_journal(sync.freee_id, body)
        else:
            client.delete_manual_journal(sync.freee_id)
    except (FreeeError, ApiError) as e:
        with transaction.atomic():
            sync = FreeeSync.objects.select_for_update().get(pk=sync_id)
            sync.status = FreeeSyncStatus.FAILED
            sync.last_error = str(getattr(e, "message", None) or e)[:500]
            sync.save(update_fields=["status", "last_error", "updated_at"])
            _log(sync, action, False, sync.last_error, getattr(e, "status", None), user)
        return sync
    with transaction.atomic():
        sync = FreeeSync.objects.select_for_update().select_related("journal").get(pk=sync_id)
        if action == "create":
            sync.freee_id = new_id
        sync.synced_hash = sending_hash
        sync.last_error = ""
        sync.synced_at = timezone.now()
        if action == "delete":
            sync.status = FreeeSyncStatus.DELETED
        elif sync.journal.content_hash != sending_hash:
            sync.status = FreeeSyncStatus.NEEDS_UPDATE  # 送信中に内容が変わった
        else:
            sync.status = FreeeSyncStatus.SYNCED
        sync.save()
        _log(sync, action, True, f"freee id={sync.freee_id}", None, user)
    return sync


def mark_stuck_as_unknown(conn: FreeeConnection, older_than_minutes: int = 10) -> int:
    """送信中のまま残ったもの（通信の途中で止まった）を「結果不明」にする。自動で再送しない。"""
    limit = timezone.now() - timedelta(minutes=older_than_minutes)
    return FreeeSync.objects.filter(journal__taxpayer=conn.taxpayer, status=FreeeSyncStatus.SENDING, last_attempt_at__lt=limit).update(
        status=FreeeSyncStatus.UNKNOWN, last_error="送信中に止まりました。freee 側に作成されていないか確認してから「確認済み・再送」してください。"
    )


def send_pending(conn: FreeeConnection, user=None, journal_ids: list[int] | None = None) -> dict:
    if conn.status != "connected":
        raise ApiError("freee_not_connected", "freee と接続されていません。", status=409)
    mark_stuck_as_unknown(conn)
    qs = FreeeSync.objects.filter(journal__taxpayer=conn.taxpayer, status__in=SENDABLE)
    if journal_ids:
        qs = qs.filter(journal_id__in=journal_ids)
    if conn.sync_from:
        qs = qs.filter(journal__issue_date__gte=conn.sync_from)
    result = {"sent": 0, "failed": 0}
    for sid in list(qs.order_by("journal__issue_date", "id").values_list("id", flat=True)[:200]):
        s = send_one(sid, conn, user)
        if s.status in (FreeeSyncStatus.SYNCED, FreeeSyncStatus.DELETED):
            result["sent"] += 1
        elif s.status == FreeeSyncStatus.FAILED:
            result["failed"] += 1
    return result


def resolve_unknown(sync: FreeeSync, *, freee_id: int | None, user=None) -> FreeeSync:
    """結果不明を解消する: freee 側にあった → その ID を登録（連携済み）、なかった → 未送信に戻す。"""
    with transaction.atomic():
        sync = FreeeSync.objects.select_for_update().select_related("journal").get(pk=sync.pk)
        if sync.status != FreeeSyncStatus.UNKNOWN:
            raise ApiError("not_unknown", "結果不明の仕訳ではありません。", status=409)
        if freee_id:
            sync.freee_id = freee_id
            sync.synced_hash = sync.journal.content_hash
            sync.status = FreeeSyncStatus.SYNCED
            sync.synced_at = timezone.now()
        else:
            sync.status = FreeeSyncStatus.PENDING if not sync.freee_id else FreeeSyncStatus.NEEDS_UPDATE
        sync.last_error = ""
        sync.save()
        _log(sync, "resolve", True, f"手動で確認: freee id={freee_id}", None, user)
    return sync
