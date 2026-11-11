from __future__ import annotations

from rest_framework import serializers

from apps.catalog.models import Product, Table
from apps.core.api import StorePK
from apps.staff.models import Employee

from .models import BottleKeep, Customer, Reservation


class CustomerSerializer(serializers.ModelSerializer):
    assigned_employee = StorePK(model=Employee, scope="company", required=False, allow_null=True)
    assigned_employee_name = serializers.CharField(source="assigned_employee.display_name", read_only=True, default=None)
    store_name = serializers.CharField(source="store.name", read_only=True)
    is_own_store = serializers.SerializerMethodField()
    active_bottles = serializers.SerializerMethodField()

    class Meta:
        model = Customer
        fields = ["id", "store", "store_name", "is_own_store", "display_name", "phone", "contact_note", "assigned_employee", "assigned_employee_name",
                  "memo", "shared_in_company", "is_active", "active_bottles", "created_at"]
        read_only_fields = ["id", "store", "created_at"]

    def get_is_own_store(self, obj):
        store = self.context.get("store")
        return store is not None and obj.store_id == store.id

    def get_active_bottles(self, obj):
        return sum(1 for b in obj.bottle_keeps.all() if b.status == "active")


class CustomerBriefSerializer(serializers.ModelSerializer):
    class Meta:
        model = Customer
        fields = ["id", "display_name", "store"]


class BottleKeepSerializer(serializers.ModelSerializer):
    product = StorePK(model=Product, required=False, allow_null=True)
    customer = StorePK(model=Customer, scope="company")
    customer_name = serializers.CharField(source="customer.display_name", read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = BottleKeep
        fields = ["id", "customer", "customer_name", "product", "name", "location", "registered_on", "expires_on", "status", "status_label",
                  "remaining_note", "source_order_line", "note", "created_at"]
        read_only_fields = ["id", "source_order_line", "created_at"]


class ReservationSerializer(serializers.ModelSerializer):
    customer = StorePK(model=Customer, scope="company", required=False, allow_null=True)
    table = StorePK(model=Table, required=False, allow_null=True)
    employee = StorePK(model=Employee, scope="company", required=False, allow_null=True)
    customer_name = serializers.CharField(source="customer.display_name", read_only=True, default=None)
    table_name = serializers.CharField(source="table.name", read_only=True, default=None)
    employee_name = serializers.CharField(source="employee.display_name", read_only=True, default=None)
    status_label = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = Reservation
        fields = ["id", "customer", "customer_name", "guest_name", "reserved_at", "guest_count", "table", "table_name", "status", "status_label",
                  "employee", "employee_name", "visit", "source", "line_user", "note", "created_at"]
        read_only_fields = ["id", "visit", "line_user", "created_at"]
