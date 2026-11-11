"""顧客・ボトルキープのサービス。"""
from __future__ import annotations

from django.db.models import Q
from django.utils import timezone

from apps.core.exceptions import ApiError

from .models import BottleKeep, Customer


def visible_customers(store):
    """
    この店舗から参照できる顧客:
      - 店舗自身の顧客
      - 会社が共有を許可し、かつ顧客が shared_in_company のもの（同一会社の他店舗）
    別会社の顧客は決して含めない。
    """
    qs = Customer.objects.filter(company=store.company)
    if store.company.customer_sharing_enabled:
        return qs.filter(Q(store=store) | Q(shared_in_company=True))
    return qs.filter(store=store)


def get_customer_for_store(store, customer_id) -> Customer:
    customer = visible_customers(store).filter(id=customer_id, is_active=True).first()
    if customer is None:
        raise ApiError("validation_error", "この店舗で利用できない顧客が指定されました。")
    return customer


def create_bottle_keep_from_line(customer: Customer, line, user, location: str = "") -> BottleKeep:
    """
    ボトル販売時にキープを登録する。販売在庫は販売時に減らしているので、ここでは減らさない。
    """
    product = line.product
    return BottleKeep.objects.create(
        store=line.store, customer=customer, product=product, name=product.name if product else line.name,
        location=location[:60], registered_on=timezone.localdate(), source_order_line=line, created_by=user,
    )
