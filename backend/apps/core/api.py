"""
店舗スコープ付き API の共通部品。

- StoreScopedViewSet: queryset を request.store で絞り、作成時に store を注入する。
- StorePK: 関連付ける ID（従業員・商品・伝票など）が同じ店舗（または同じ会社）に属することを検証する。
"""
from __future__ import annotations

from rest_framework import serializers, viewsets

from .exceptions import ApiError
from .permissions import StoreCaps, ensure_store_access


def current_store(request):
    return ensure_store_access(request).store


class StorePK(serializers.PrimaryKeyRelatedField):
    """
    店舗に属するモデルの PK フィールド。request の店舗に属するものだけを受け付ける。
    scope="store": model.store == request.store
    scope="company": model.company == request.store.company（従業員など）
    """

    def __init__(self, *, model=None, scope: str = "store", extra_filter=None, **kwargs):
        self._model = model
        self._scope = scope
        self._extra_filter = extra_filter
        kwargs.setdefault("queryset", model._default_manager.none() if model is not None else None)
        super().__init__(**kwargs)

    def get_queryset(self):
        request = self.context.get("request")
        if request is None:
            return self._model._default_manager.none()
        store = current_store(request)
        qs = self._model._default_manager.all()
        if self._scope == "store":
            qs = qs.filter(store=store)
        elif self._scope == "company":
            qs = qs.filter(company=store.company)
        if self._extra_filter:
            qs = qs.filter(**self._extra_filter)
        return qs

    def to_internal_value(self, data):
        try:
            return super().to_internal_value(data)
        except serializers.ValidationError:
            raise serializers.ValidationError("この店舗で利用できない対象が指定されました。")


class StoreScopedViewSet(viewsets.ModelViewSet):
    """
    permission_classes には StoreCaps(...) を指定する。
    queryset は model._default_manager をもとに request.store で絞る。
    """

    model = None
    store_field = "store"
    caps_read: tuple[str, ...] = ()
    caps_write: tuple[str, ...] = ()

    def get_permissions(self):
        return [StoreCaps(*self.caps_read, write_caps=self.caps_write)]

    def get_queryset(self):
        store = current_store(self.request)
        qs = self.model._default_manager.all()
        return qs.filter(**{self.store_field: store})

    def perform_create(self, serializer):
        store = current_store(self.request)
        kwargs = {self.store_field: store}
        if "created_by" in [f.name for f in self.model._meta.get_fields()]:
            kwargs["created_by"] = self.request.user
        serializer.save(**kwargs)

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        ctx["store"] = current_store(self.request)
        return ctx


def require(condition: bool, code: str, message: str, status: int = 400):
    if not condition:
        raise ApiError(code, message, status=status)
