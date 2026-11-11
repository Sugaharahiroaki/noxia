"""
API エラーの統一形式:
  {"error": {"code": "...", "message": "日本語の説明", "details": {...}}}
"""
from __future__ import annotations

import logging

from django.core.exceptions import PermissionDenied, ValidationError as DjangoValidationError
from django.http import Http404
from rest_framework import status
from rest_framework.exceptions import APIException, AuthenticationFailed, NotAuthenticated, PermissionDenied as DrfPermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

log = logging.getLogger("yorumiru.api")


class ApiError(APIException):
    status_code = status.HTTP_400_BAD_REQUEST
    default_code = "error"

    def __init__(self, code: str, message: str, *, status: int = 400, details=None):
        self.status_code = status
        self.code = code
        self.message = message
        self.details = details
        super().__init__(detail=message, code=code)

    def payload(self):
        body = {"code": self.code, "message": self.message}
        if self.details is not None:
            body["details"] = self.details
        return {"error": body}


def _flatten_validation(detail) -> str:
    """DRF ValidationError の detail を人が読める1行にする。"""
    if isinstance(detail, dict):
        parts = []
        for k, v in detail.items():
            parts.append(f"{k}: {_flatten_validation(v)}")
        return " / ".join(parts)
    if isinstance(detail, list):
        return "、".join(_flatten_validation(v) for v in detail)
    return str(detail)


def exception_handler(exc, context):
    if isinstance(exc, ApiError):
        return Response(exc.payload(), status=exc.status_code)
    if isinstance(exc, ValidationError):
        return Response(
            {"error": {"code": "validation_error", "message": "入力内容を確認してください: " + _flatten_validation(exc.detail), "details": exc.detail}},
            status=400,
        )
    if isinstance(exc, DjangoValidationError):
        return Response({"error": {"code": "validation_error", "message": "入力内容を確認してください: " + "、".join(exc.messages)}}, status=400)
    if isinstance(exc, (NotAuthenticated, AuthenticationFailed)):
        return Response({"error": {"code": "auth_required", "message": "ログインが必要です。"}}, status=401)
    if isinstance(exc, (DrfPermissionDenied, PermissionDenied)):
        msg = str(getattr(exc, "detail", "")) or "この操作を行う権限がありません。"
        if "CSRF" in msg:
            return Response({"error": {"code": "csrf_failed", "message": "セキュリティトークンが無効です。画面を再読み込みしてください。"}}, status=403)
        return Response({"error": {"code": "forbidden", "message": "この操作を行う権限がありません。"}}, status=403)
    if isinstance(exc, Http404):
        return Response({"error": {"code": "not_found", "message": "対象が見つかりません。"}}, status=404)
    response = drf_exception_handler(exc, context)
    if response is not None:
        detail = response.data.get("detail") if isinstance(response.data, dict) else response.data
        return Response({"error": {"code": getattr(exc, "default_code", "error"), "message": str(detail)}}, status=response.status_code)
    log.exception("unhandled error")
    return Response({"error": {"code": "server_error", "message": "サーバーでエラーが発生しました。保存されたかどうかは画面を再読み込みして確認してください。"}}, status=500)
