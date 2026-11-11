"""操作履歴の記録。"""
from __future__ import annotations

import json

from rest_framework.utils.encoders import JSONEncoder

from .models import AuditLog


def _jsonable(value):
    if value is None:
        return {}
    return json.loads(json.dumps(value, cls=JSONEncoder))


def audit(request, action: str, *, target=None, target_type: str = "", target_id="", changes=None, store=None, company=None):
    """
    request: DRF Request（user / store_access / request_id を参照）
    target: モデルインスタンス（型と ID を自動で記録）
    changes: 変更内容（認証情報・不要な個人情報は渡さないこと）
    """
    user = getattr(request, "user", None)
    if user is not None and not getattr(user, "is_authenticated", False):
        user = None
    access = getattr(request, "store_access", None)
    if store is None and access is not None:
        store = access.store
    if company is None:
        if store is not None:
            company = store.company
        elif access is not None:
            company = access.company
    if target is not None:
        target_type = target_type or type(target).__name__
        target_id = target_id or str(getattr(target, "pk", ""))
    AuditLog.objects.create(
        company=company,
        store=store,
        user=user,
        action=action,
        target_type=target_type,
        target_id=str(target_id),
        changes=_jsonable(changes),
        request_id=getattr(request, "request_id", "") or getattr(getattr(request, "_request", None), "request_id", ""),
    )
