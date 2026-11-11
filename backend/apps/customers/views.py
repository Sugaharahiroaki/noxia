from __future__ import annotations

from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_date
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.api import StoreScopedViewSet, current_store
from apps.core.audit import audit
from apps.core.exceptions import ApiError
from apps.core.idempotency import idempotent
from apps.core.permissions import CAP_CUSTOMERS_EDIT, CAP_CUSTOMERS_VIEW, CAP_FLOOR, CAP_RESERVATIONS, StoreCaps
from apps.sales import services as sales_services
from apps.sales.models import Checkout
from apps.sales.serializers import CheckoutBriefSerializer, VisitBriefSerializer, VisitSerializer

from . import services
from .models import BottleKeep, Customer, Reservation
from .serializers import BottleKeepSerializer, CustomerSerializer, ReservationSerializer


class CustomerViewSet(StoreScopedViewSet):
    model = Customer
    serializer_class = CustomerSerializer
    caps_read = (CAP_CUSTOMERS_VIEW,)
    caps_write = (CAP_CUSTOMERS_EDIT,)

    def get_queryset(self):
        store = current_store(self.request)
        qs = services.visible_customers(store).select_related("store", "assigned_employee").prefetch_related("bottle_keeps")
        q = self.request.query_params.get("q")
        if q:
            qs = qs.filter(display_name__icontains=q)
        if self.request.query_params.get("active", "1") == "1":
            qs = qs.filter(is_active=True)
        return qs

    def perform_create(self, serializer):
        store = current_store(self.request)
        serializer.save(store=store, company=store.company, created_by=self.request.user)
        audit(self.request, "customer.create", target=serializer.instance, changes={"display_name": serializer.instance.display_name})

    def perform_update(self, serializer):
        obj = serializer.instance
        if obj.store_id != current_store(self.request).id:
            raise ApiError("forbidden", "他店舗の顧客情報は編集できません。", status=403)
        serializer.save()
        audit(self.request, "customer.update", target=obj)

    def destroy(self, request, *args, **kwargs):
        obj = self.get_object()
        if obj.store_id != current_store(request).id:
            raise ApiError("forbidden", "他店舗の顧客情報は編集できません。", status=403)
        obj.is_active = False
        obj.save(update_fields=["is_active"])
        audit(request, "customer.deactivate", target=obj)
        return Response(status=204)


class CustomerHistoryView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_CUSTOMERS_VIEW)]

    def get(self, request, customer_id):
        store = request.store
        customer = services.visible_customers(store).filter(id=customer_id).first()
        if customer is None:
            raise ApiError("not_found", "顧客が見つかりません。", status=404)
        # 来店・会計履歴はこの店舗分だけ（他店舗の売上は見せない）
        visits = customer.visits.filter(store=store).select_related("table", "primary_employee").prefetch_related("course_entries", "lines")[:50]
        checkouts = Checkout.objects.filter(store=store, visit__customer=customer).prefetch_related("payments")[:50]
        bottles = customer.bottle_keeps.filter(store=store)
        return Response({
            "customer": CustomerSerializer(customer, context={"store": store}).data,
            "visits": VisitBriefSerializer(visits, many=True).data,
            "checkouts": CheckoutBriefSerializer(checkouts, many=True).data,
            "bottles": BottleKeepSerializer(bottles, many=True).data,
        })


class BottleKeepViewSet(StoreScopedViewSet):
    model = BottleKeep
    serializer_class = BottleKeepSerializer
    caps_read = (CAP_CUSTOMERS_VIEW,)
    caps_write = (CAP_CUSTOMERS_EDIT,)

    def get_queryset(self):
        qs = super().get_queryset().select_related("customer", "product")
        status_q = self.request.query_params.get("status")
        if status_q:
            qs = qs.filter(status=status_q)
        if self.request.query_params.get("customer"):
            qs = qs.filter(customer_id=self.request.query_params["customer"])
        return qs

    def perform_create(self, serializer):
        # 手動登録（販売在庫は減らさない。販売時に減っている／持ち込み等）
        super().perform_create(serializer)
        audit(self.request, "bottle_keep.create", target=serializer.instance, changes={"name": serializer.instance.name})

    def perform_update(self, serializer):
        serializer.save()
        audit(self.request, "bottle_keep.update", target=serializer.instance, changes={"status": serializer.instance.status})


class ReservationViewSet(StoreScopedViewSet):
    model = Reservation
    serializer_class = ReservationSerializer
    caps_read = (CAP_RESERVATIONS,)
    caps_write = (CAP_RESERVATIONS,)

    def get_queryset(self):
        qs = super().get_queryset().select_related("customer", "table", "employee")
        d = parse_date(self.request.query_params.get("date") or "")
        if d:
            qs = qs.filter(reserved_at__date=d)
        f = parse_date(self.request.query_params.get("from") or "")
        if f:
            qs = qs.filter(reserved_at__date__gte=f)
        status_q = self.request.query_params.get("status")
        if status_q:
            qs = qs.filter(status=status_q)
        return qs

    def perform_create(self, serializer):
        super().perform_create(serializer)
        audit(self.request, "reservation.create", target=serializer.instance, changes={"guest_name": serializer.instance.guest_name})

    def perform_update(self, serializer):
        serializer.save()
        audit(self.request, "reservation.update", target=serializer.instance, changes={"status": serializer.instance.status})


class ReservationSeatView(APIView):
    """予約から入店登録へ引き継ぐ。"""

    def get_permissions(self):
        return [StoreCaps(CAP_FLOOR)]

    @idempotent
    @transaction.atomic
    def post(self, request, reservation_id):
        res = Reservation.objects.select_for_update().filter(id=reservation_id, store=request.store).first()
        if res is None:
            raise ApiError("not_found", "予約が見つかりません。", status=404)
        if res.status in ("seated", "cancelled", "no_show"):
            raise ApiError("invalid_state", "この予約は入店済みまたはキャンセルされています。", status=409)
        data = dict(request.data)
        data.setdefault("table_id", res.table_id)
        data.setdefault("customer_id", res.customer_id)
        data.setdefault("customer_name", res.guest_name)
        data.setdefault("primary_employee_id", res.employee_id)
        visit = sales_services.open_visit(request.store, request.user, data)
        res.status = "seated"
        res.visit = visit
        res.save(update_fields=["status", "visit", "updated_at"])
        audit(request, "reservation.seat", target=res, changes={"visit_id": visit.id})
        return Response(VisitSerializer(visit).data, status=201)
