"""
顧客・ボトルキープ・予約。

- 顧客は店舗ごとに管理する。会社が customer_sharing_enabled かつ顧客が shared_in_company のときだけ、
  同一会社内の他店舗から参照できる。別会社との共有は不可。
- キープボトルは販売用在庫とは分離する（販売時に在庫を減らし、キープ登録しても再度は減らさない）。
"""
from __future__ import annotations

from django.conf import settings
from django.db import models

from apps.core.models import Company, Store, TimestampedModel


class Customer(TimestampedModel):
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="customers")
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="customers")
    display_name = models.CharField("表示名", max_length=80)
    phone = models.CharField("連絡先（任意）", max_length=40, blank=True)
    contact_note = models.CharField("連絡先メモ（任意）", max_length=120, blank=True)
    assigned_employee = models.ForeignKey("staff.Employee", null=True, blank=True, on_delete=models.SET_NULL, related_name="customers")
    memo = models.TextField(blank=True)
    shared_in_company = models.BooleanField("会社内で共有する", default=False)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["display_name", "id"]
        indexes = [models.Index(fields=["store", "is_active"]), models.Index(fields=["company", "shared_in_company"])]

    def __str__(self) -> str:
        return self.display_name


class BottleKeepStatus(models.TextChoices):
    ACTIVE = "active", "保管中"
    CONSUMED = "consumed", "飲み切り"
    EXPIRED = "expired", "期限切れ"
    DISPOSED = "disposed", "処分済"


class BottleKeep(TimestampedModel):
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="bottle_keeps")
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name="bottle_keeps")
    product = models.ForeignKey("catalog.Product", null=True, blank=True, on_delete=models.SET_NULL, related_name="bottle_keeps")
    name = models.CharField("銘柄", max_length=100)
    location = models.CharField("保管場所", max_length=60, blank=True)
    registered_on = models.DateField("登録日")
    expires_on = models.DateField("期限", null=True, blank=True)
    status = models.CharField(max_length=20, choices=BottleKeepStatus.choices, default=BottleKeepStatus.ACTIVE)
    remaining_note = models.CharField("残量メモ", max_length=60, blank=True)
    source_order_line = models.ForeignKey("sales.OrderLine", null=True, blank=True, on_delete=models.SET_NULL, related_name="bottle_keeps")
    note = models.CharField(max_length=200, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-registered_on", "-id"]
        indexes = [models.Index(fields=["store", "status"])]


class ReservationStatus(models.TextChoices):
    PENDING = "pending", "仮予約"
    CONFIRMED = "confirmed", "確定"
    SEATED = "seated", "来店済（入店登録済）"
    CANCELLED = "cancelled", "キャンセル"
    NO_SHOW = "no_show", "無断キャンセル"


class Reservation(TimestampedModel):
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="reservations")
    customer = models.ForeignKey(Customer, null=True, blank=True, on_delete=models.SET_NULL, related_name="reservations")
    guest_name = models.CharField("予約名", max_length=80)
    reserved_at = models.DateTimeField("予約日時")
    guest_count = models.PositiveSmallIntegerField(default=1)
    table = models.ForeignKey("catalog.Table", null=True, blank=True, on_delete=models.SET_NULL, related_name="reservations")
    status = models.CharField(max_length=20, choices=ReservationStatus.choices, default=ReservationStatus.PENDING)
    employee = models.ForeignKey("staff.Employee", null=True, blank=True, on_delete=models.SET_NULL, related_name="reservations")
    visit = models.OneToOneField("sales.Visit", null=True, blank=True, on_delete=models.SET_NULL, related_name="reservation")
    source = models.CharField(max_length=20, default="manual")  # manual / line / phone
    line_user = models.ForeignKey("line_integration.LineUser", null=True, blank=True, on_delete=models.SET_NULL, related_name="reservations")
    note = models.CharField(max_length=300, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["reserved_at"]
        indexes = [models.Index(fields=["store", "reserved_at"]), models.Index(fields=["store", "status"])]
