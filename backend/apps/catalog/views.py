from __future__ import annotations

from rest_framework.response import Response

from apps.core.api import StoreScopedViewSet
from apps.core.audit import audit
from apps.core.exceptions import ApiError
from apps.core.permissions import CAP_CATALOG_EDIT, CAP_SETTINGS_VIEW

from .models import Category, Course, Product, Table
from .serializers import CategorySerializer, CourseSerializer, ProductSerializer, TableSerializer


class _CatalogViewSet(StoreScopedViewSet):
    caps_read = (CAP_SETTINGS_VIEW,)
    caps_write = (CAP_CATALOG_EDIT,)
    audit_prefix = ""

    def perform_create(self, serializer):
        super().perform_create(serializer)
        audit(self.request, f"{self.audit_prefix}.create", target=serializer.instance, changes=serializer.data)

    def perform_update(self, serializer):
        serializer.save()
        audit(self.request, f"{self.audit_prefix}.update", target=serializer.instance, changes=serializer.data)

    def destroy(self, request, *args, **kwargs):
        # マスタは削除せず停止にする（過去の明細との整合性を守る）
        obj = self.get_object()
        if hasattr(obj, "is_active"):
            obj.is_active = False
            obj.save(update_fields=["is_active"])
            audit(request, f"{self.audit_prefix}.deactivate", target=obj)
            return Response(status=204)
        raise ApiError("not_allowed", "削除はできません。", status=405)

    def get_queryset(self):
        qs = super().get_queryset()
        if self.request.query_params.get("active") == "1":
            qs = qs.filter(is_active=True)
        return qs


class CourseViewSet(_CatalogViewSet):
    model = Course
    serializer_class = CourseSerializer
    audit_prefix = "catalog.course"


class CategoryViewSet(_CatalogViewSet):
    model = Category
    serializer_class = CategorySerializer
    audit_prefix = "catalog.category"


class ProductViewSet(_CatalogViewSet):
    model = Product
    serializer_class = ProductSerializer
    audit_prefix = "catalog.product"

    def get_queryset(self):
        return super().get_queryset().select_related("category")


class TableViewSet(_CatalogViewSet):
    model = Table
    serializer_class = TableSerializer
    audit_prefix = "catalog.table"
