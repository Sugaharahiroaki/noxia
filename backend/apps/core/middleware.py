from __future__ import annotations

import uuid


class RequestIdMiddleware:
    """リクエストごとの識別子を付与し、監査ログ・レスポンスヘッダに載せる。"""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.request_id = uuid.uuid4().hex[:12]
        response = self.get_response(request)
        response["X-Request-Id"] = request.request_id
        return response
