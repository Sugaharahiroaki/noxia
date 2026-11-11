"""
二重登録防止（Idempotency-Key）。

- 更新系の view メソッドに @idempotent を付ける。
- クライアントは操作ごとに一意なキー（UUID）を生成し、再送時も同じキーを使う。
- 同じキーの処理が完了済みなら最初の結果をそのまま返す。処理中なら 409 を返す。
- キーが同じで内容が異なる場合は 422 で拒否する。
"""
from __future__ import annotations

import hashlib
import json
from functools import wraps

from django.db import IntegrityError, transaction
from rest_framework.response import Response
from rest_framework.utils.encoders import JSONEncoder

from .models import IdempotencyRecord


def _request_hash(request) -> str:
    body = request.body or b""
    h = hashlib.sha256()
    h.update(request.method.encode())
    h.update(b"\n")
    h.update(request.path.encode())
    h.update(b"\n")
    h.update(body)
    return h.hexdigest()


def idempotent(view_func):
    @wraps(view_func)
    def wrapper(self, request, *args, **kwargs):
        key = request.headers.get("Idempotency-Key")
        if not key:
            return view_func(self, request, *args, **kwargs)
        key = key.strip()[:80]
        req_hash = _request_hash(request)
        store = getattr(request, "store", None)
        try:
            with transaction.atomic():
                record = IdempotencyRecord.objects.create(
                    user=request.user, store=store, key=key, method=request.method, path=request.path[:300], request_hash=req_hash
                )
        except IntegrityError:
            existing = IdempotencyRecord.objects.filter(user=request.user, key=key).first()
            if existing is None:
                return Response({"error": {"code": "conflict", "message": "同じ操作が処理中です。少し待ってから画面を更新して状態を確認してください。"}}, status=409)
            if existing.request_hash != req_hash:
                return Response({"error": {"code": "idempotency_mismatch", "message": "同じ操作キーで異なる内容が送信されました。画面を更新してやり直してください。"}}, status=422)
            if existing.state == IdempotencyRecord.STATE_DONE:
                resp = Response(existing.response_body, status=existing.status_code)
                resp["Idempotent-Replayed"] = "true"
                return resp
            return Response({"error": {"code": "in_progress", "message": "同じ操作が処理中です。少し待ってから画面を更新して状態を確認してください。"}}, status=409)

        try:
            response = view_func(self, request, *args, **kwargs)
        except Exception:
            IdempotencyRecord.objects.filter(pk=record.pk).delete()
            raise
        if 200 <= response.status_code < 300:
            # Decimal / datetime などを JSON 化して保存する（再送時は同じ JSON を返す）
            data = json.loads(json.dumps(response.data, cls=JSONEncoder))
            IdempotencyRecord.objects.filter(pk=record.pk).update(
                state=IdempotencyRecord.STATE_DONE, status_code=response.status_code, response_body=data
            )
        else:
            # エラー応答は記録せず、修正後の再送を許す
            IdempotencyRecord.objects.filter(pk=record.pk).delete()
        return response

    return wrapper
