from __future__ import annotations

from rest_framework import serializers

from .models import Checkout, CourseEntry, CourseEvent, OrderLine, OrderLineBackTarget, Payment, Receivable, ReceivableCollection, Refund, Visit


class BackTargetSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(source="employee.display_name", read_only=True)

    class Meta:
        model = OrderLineBackTarget
        fields = ["employee", "employee_name", "share_type", "share_value"]


class OrderLineSerializer(serializers.ModelSerializer):
    back_targets = BackTargetSerializer(many=True, read_only=True)
    line_type_label = serializers.CharField(source="get_line_type_display", read_only=True)
    cancelled_by_name = serializers.SerializerMethodField()

    class Meta:
        model = OrderLine
        fields = ["id", "visit", "line_type", "line_type_label", "course_entry", "product", "name", "unit_price", "quantity", "amount",
                  "tax_rate", "tax_included", "category_kind", "status", "cancel_reason", "cancelled_by_name", "cancelled_at",
                  "discount_reason", "checkout", "note", "back_targets", "created_at"]

    def get_cancelled_by_name(self, obj):
        return str(obj.cancelled_by) if obj.cancelled_by else ""


class CourseEventSerializer(serializers.ModelSerializer):
    performed_by_name = serializers.SerializerMethodField()

    class Meta:
        model = CourseEvent
        fields = ["id", "kind", "performed_by_name", "performed_at", "from_planned_end_at", "to_planned_end_at", "amount", "note"]

    def get_performed_by_name(self, obj):
        return str(obj.performed_by) if obj.performed_by else ""


class CourseEntrySerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(source="employee.display_name", read_only=True, default=None)
    events = CourseEventSerializer(many=True, read_only=True)
    alarm = serializers.SerializerMethodField()

    class Meta:
        model = CourseEntry
        fields = ["id", "visit", "label", "guest_count", "course", "course_name", "course_price", "extension_price", "extension_minutes",
                  "started_at", "planned_end_at", "is_unlimited", "extension_count", "ended_at", "employee", "employee_name", "is_active",
                  "alarm", "events"]

    def get_alarm(self, obj):
        return self.context.get("alarms", {}).get(obj.id)


class VisitSerializer(serializers.ModelSerializer):
    table_name = serializers.CharField(source="table.name", read_only=True)
    customer_display = serializers.SerializerMethodField()
    primary_employee_name = serializers.CharField(source="primary_employee.display_name", read_only=True, default=None)
    course_entries = CourseEntrySerializer(many=True, read_only=True)
    lines = OrderLineSerializer(many=True, read_only=True)
    checkouts = serializers.SerializerMethodField()
    status_label = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = Visit
        fields = ["id", "business_date", "slip_number", "table", "table_name", "status", "status_label", "opened_at", "closed_at",
                  "guest_count", "customer", "customer_name", "customer_display", "primary_employee", "primary_employee_name", "note",
                  "cancel_reason", "course_entries", "lines", "checkouts", "created_at"]

    def get_customer_display(self, obj):
        if obj.customer_id and obj.customer:
            return obj.customer.display_name
        return obj.customer_name

    def get_checkouts(self, obj):
        return CheckoutBriefSerializer(obj.checkouts.all(), many=True).data


class VisitBriefSerializer(serializers.ModelSerializer):
    table_name = serializers.CharField(source="table.name", read_only=True)
    customer_display = serializers.SerializerMethodField()
    primary_employee_name = serializers.CharField(source="primary_employee.display_name", read_only=True, default=None)
    course_entries = CourseEntrySerializer(many=True, read_only=True)
    line_count = serializers.SerializerMethodField()
    running_total = serializers.SerializerMethodField()

    class Meta:
        model = Visit
        fields = ["id", "business_date", "slip_number", "table", "table_name", "status", "opened_at", "guest_count", "customer_display",
                  "primary_employee_name", "note", "course_entries", "line_count", "running_total"]

    def get_customer_display(self, obj):
        if obj.customer_id and obj.customer:
            return obj.customer.display_name
        return obj.customer_name

    def get_line_count(self, obj):
        return sum(1 for l in obj.lines.all() if l.status == "active")

    def get_running_total(self, obj):
        totals = self.context.get("running_totals", {})
        return totals.get(obj.id)


class PaymentSerializer(serializers.ModelSerializer):
    method_label = serializers.CharField(source="get_method_display", read_only=True)

    class Meta:
        model = Payment
        fields = ["id", "method", "method_label", "amount", "tendered", "change_amount", "paid_at", "status", "card_reference", "note", "business_date"]


class RefundSerializer(serializers.ModelSerializer):
    method_label = serializers.CharField(source="get_method_display", read_only=True)
    recorded_by_name = serializers.SerializerMethodField()

    class Meta:
        model = Refund
        fields = ["id", "checkout", "payment", "business_date", "method", "method_label", "amount", "reason", "refunded_at", "recorded_by_name",
                  "terminal_refund_done", "note"]

    def get_recorded_by_name(self, obj):
        return str(obj.recorded_by) if obj.recorded_by else ""


class CheckoutBriefSerializer(serializers.ModelSerializer):
    payments = PaymentSerializer(many=True, read_only=True)

    class Meta:
        model = Checkout
        fields = ["id", "number", "kind", "status", "business_date", "confirmed_at", "total", "payments"]


class CheckoutSerializer(serializers.ModelSerializer):
    payments = PaymentSerializer(many=True, read_only=True)
    refunds = RefundSerializer(many=True, read_only=True)
    lines = OrderLineSerializer(many=True, read_only=True)
    visit_slip_number = serializers.IntegerField(source="visit.slip_number", read_only=True)
    visit_table_name = serializers.CharField(source="visit.table.name", read_only=True)
    confirmed_by_name = serializers.SerializerMethodField()
    voided_by_name = serializers.SerializerMethodField()
    status_label = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = Checkout
        fields = ["id", "visit", "visit_slip_number", "visit_table_name", "number", "kind", "status", "status_label", "business_date", "confirmed_at",
                  "confirmed_by_name", "subtotal", "discount_total", "service_charge", "tax_total", "tax_breakdown", "total_before_rounding",
                  "rounding_adjustment", "total", "total_excl_tax", "breakdown", "settings_snapshot", "void_reason", "voided_at", "voided_by_name",
                  "note", "payments", "refunds", "lines"]

    def get_confirmed_by_name(self, obj):
        return str(obj.confirmed_by) if obj.confirmed_by else ""

    def get_voided_by_name(self, obj):
        return str(obj.voided_by) if obj.voided_by else ""


class ReceivableCollectionSerializer(serializers.ModelSerializer):
    class Meta:
        model = ReceivableCollection
        fields = ["id", "business_date", "method", "amount", "collected_at", "note"]


class ReceivableSerializer(serializers.ModelSerializer):
    collections = ReceivableCollectionSerializer(many=True, read_only=True)
    checkout_number = serializers.IntegerField(source="checkout.number", read_only=True)
    customer_name = serializers.CharField(source="customer.display_name", read_only=True, default=None)
    remaining = serializers.SerializerMethodField()

    class Meta:
        model = Receivable
        fields = ["id", "checkout", "checkout_number", "customer", "customer_name", "debtor_name", "amount", "collected_amount", "remaining",
                  "status", "business_date", "note", "collections", "created_at"]

    def get_remaining(self, obj):
        return str(obj.amount - obj.collected_amount)
