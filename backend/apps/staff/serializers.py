from __future__ import annotations

from rest_framework import serializers

from apps.core.api import StorePK

from .models import (
    BackEntry, BackRule, Employee, EmployeePayment, EmployeeStoreAssignment, PayPeriod, PayStatement, Shift, ShiftBreak,
    ShiftCorrection, WageSetting,
)


class AssignmentSerializer(serializers.ModelSerializer):
    store_name = serializers.CharField(source="store.name", read_only=True)

    class Meta:
        model = EmployeeStoreAssignment
        fields = ["id", "store", "store_name", "role", "is_active", "started_on", "ended_on"]


class EmployeeSerializer(serializers.ModelSerializer):
    assignments = AssignmentSerializer(many=True, read_only=True)
    user_username = serializers.CharField(source="user.username", read_only=True, default=None)
    login_enabled = serializers.SerializerMethodField()
    this_store_role = serializers.SerializerMethodField()
    # この店舗での今の出勤状態（一覧取得時のみ）: working / on_break / left（今日は退勤済み）/ off（未出勤）
    shift_status = serializers.SerializerMethodField()
    shift_since = serializers.SerializerMethodField()

    class Meta:
        model = Employee
        fields = ["id", "full_name", "display_name", "hired_on", "left_on", "is_active", "note", "user", "user_username", "login_enabled",
                  "assignments", "this_store_role", "shift_status", "shift_since", "created_at"]
        read_only_fields = ["id", "user", "created_at"]

    def get_shift_status(self, obj):
        shifts = self.context.get("shift_info")
        if shifts is None:
            return None
        return shifts.get(obj.id, ("off", None))[0]

    def get_shift_since(self, obj):
        shifts = self.context.get("shift_info")
        if shifts is None:
            return None
        t = shifts.get(obj.id, ("off", None))[1]
        return t.isoformat() if t else None

    def get_login_enabled(self, obj):
        return bool(obj.user_id and obj.user and obj.user.is_active)

    def get_this_store_role(self, obj):
        store = self.context.get("store")
        if store is None:
            return None
        for a in obj.assignments.all():
            if a.store_id == store.id and a.is_active:
                return a.role
        return None


class WageSettingSerializer(serializers.ModelSerializer):
    created_by_name = serializers.SerializerMethodField()

    class Meta:
        model = WageSetting
        fields = ["id", "employee", "hourly_wage", "effective_from", "note", "created_by_name", "created_at"]
        read_only_fields = ["id", "employee", "created_at"]

    def get_created_by_name(self, obj):
        return str(obj.created_by) if obj.created_by else ""


class BackRuleSerializer(serializers.ModelSerializer):
    employee = StorePK(model=Employee, scope="company", required=False, allow_null=True)
    employee_name = serializers.CharField(source="employee.display_name", read_only=True, default=None)
    category_name = serializers.CharField(source="category.name", read_only=True, default=None)
    product_name = serializers.CharField(source="product.name", read_only=True, default=None)

    class Meta:
        model = BackRule
        fields = ["id", "employee", "employee_name", "name", "target_type", "category_kind", "category", "category_name", "product", "product_name",
                  "calc_type", "value", "basis", "include_discount", "include_service_charge", "rounding", "confirm_timing",
                  "effective_from", "effective_to", "is_active", "note", "created_at"]
        read_only_fields = ["id", "created_at"]

    def validate(self, attrs):
        store = self.context.get("store")
        tt = attrs.get("target_type", getattr(self.instance, "target_type", None))
        cat = attrs.get("category", getattr(self.instance, "category", None))
        prod = attrs.get("product", getattr(self.instance, "product", None))
        if tt == "category" and cat is None:
            raise serializers.ValidationError({"category": "カテゴリを指定してください。"})
        if tt == "product" and prod is None:
            raise serializers.ValidationError({"product": "商品を指定してください。"})
        if tt == "category_kind" and not attrs.get("category_kind", getattr(self.instance, "category_kind", "")):
            raise serializers.ValidationError({"category_kind": "カテゴリ種別を指定してください。"})
        if store is not None:
            if cat is not None and cat.store_id != store.id:
                raise serializers.ValidationError({"category": "この店舗のカテゴリを指定してください。"})
            if prod is not None and prod.store_id != store.id:
                raise serializers.ValidationError({"product": "この店舗の商品を指定してください。"})
        value = attrs.get("value", getattr(self.instance, "value", None))
        calc = attrs.get("calc_type", getattr(self.instance, "calc_type", None))
        if value is not None and value < 0:
            raise serializers.ValidationError({"value": "0以上で入力してください。"})
        if calc == "percent" and value is not None and value > 100:
            raise serializers.ValidationError({"value": "割合は100%以下で入力してください。"})
        return attrs


class BackEntrySerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(source="employee.display_name", read_only=True)
    visit_slip_number = serializers.IntegerField(source="visit.slip_number", read_only=True, default=None)
    line_name = serializers.CharField(source="order_line.name", read_only=True, default=None)
    status_label = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = BackEntry
        fields = ["id", "employee", "employee_name", "visit", "visit_slip_number", "order_line", "line_name", "checkout", "kind", "business_date",
                  "basis_amount", "share_percent", "amount", "rule", "rule_snapshot", "status", "status_label", "confirm_timing", "confirmed_at",
                  "reversal_of", "reason", "created_at"]


class ShiftBreakSerializer(serializers.ModelSerializer):
    class Meta:
        model = ShiftBreak
        fields = ["id", "started_at", "ended_at"]


class ShiftCorrectionSerializer(serializers.ModelSerializer):
    corrected_by_name = serializers.SerializerMethodField()

    class Meta:
        model = ShiftCorrection
        fields = ["id", "before", "after", "reason", "corrected_by_name", "corrected_at"]

    def get_corrected_by_name(self, obj):
        return str(obj.corrected_by) if obj.corrected_by else ""


class ShiftSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(source="employee.display_name", read_only=True)
    breaks = ShiftBreakSerializer(many=True, read_only=True)
    corrections = ShiftCorrectionSerializer(many=True, read_only=True)
    worked_minutes = serializers.SerializerMethodField()

    class Meta:
        model = Shift
        fields = ["id", "employee", "employee_name", "business_date", "clock_in", "clock_out", "note", "breaks", "corrections", "worked_minutes"]

    def get_worked_minutes(self, obj):
        from .payroll import shift_minutes

        return shift_minutes(obj)


class EmployeePaymentSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(source="employee.display_name", read_only=True)
    kind_label = serializers.CharField(source="get_kind_display", read_only=True)

    class Meta:
        model = EmployeePayment
        fields = ["id", "employee", "employee_name", "kind", "kind_label", "amount", "withholding_amount", "deduction_amount", "method", "paid_at",
                  "business_date", "pay_period", "cash_movement", "is_voided", "void_reason", "note", "created_at"]


class PayStatementSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(source="employee.display_name", read_only=True)

    class Meta:
        model = PayStatement
        fields = ["id", "employee", "employee_name", "worked_minutes", "wage_amount", "back_amount", "gross_amount", "paid_amount",
                  "balance_amount", "details", "created_at"]


class PayPeriodSerializer(serializers.ModelSerializer):
    statements = PayStatementSerializer(many=True, read_only=True)

    class Meta:
        model = PayPeriod
        fields = ["id", "start_date", "end_date", "pay_date", "status", "closed_at", "note", "statements", "created_at"]
        read_only_fields = ["id", "status", "closed_at", "created_at"]
