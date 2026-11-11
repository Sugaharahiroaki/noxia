from __future__ import annotations

import secrets
from decimal import Decimal, InvalidOperation

from rest_framework import serializers

from .models import AuditLog, Company, CompanyMembership, Invitation, Store, StoreMembership, StoreSettings, User


class UserBriefSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["id", "username", "display_name", "is_platform_operator", "is_active"]
        read_only_fields = fields


class CompanySerializer(serializers.ModelSerializer):
    class Meta:
        model = Company
        fields = ["id", "name", "code", "is_active", "customer_sharing_enabled", "is_demo", "note", "created_at"]
        read_only_fields = ["id", "created_at"]


class StoreSerializer(serializers.ModelSerializer):
    company_name = serializers.CharField(source="company.name", read_only=True)

    class Meta:
        model = Store
        fields = ["id", "company", "company_name", "name", "code", "is_active", "is_demo", "timezone", "created_at"]
        read_only_fields = ["id", "company", "company_name", "created_at"]


class StoreSettingsSerializer(serializers.ModelSerializer):
    default_unlimited_course_name = serializers.CharField(source="default_unlimited_course.name", read_only=True, default=None)

    class Meta:
        model = StoreSettings
        exclude = ["store", "created_at", "updated_at"]

    def validate_default_unlimited_course(self, course):
        if course is None:
            return course
        store = self.context.get("store")
        if store is not None and course.store_id != store.id:
            raise serializers.ValidationError("この店舗のコースを指定してください。")
        if course.duration_minutes is not None:
            raise serializers.ValidationError("時間無制限のコースを指定してください。")
        return course

    def validate_payout_deductions(self, value):
        """日払いの控除項目。code は付け外しの記録に使うので、既存の項目は変えずに残し、新しい項目にだけ振る。"""
        if not isinstance(value, list):
            raise serializers.ValidationError("控除項目の一覧を指定してください。")
        if len(value) > 10:
            raise serializers.ValidationError("控除項目は10個までです。")
        out, codes = [], set()
        for row in value:
            if not isinstance(row, dict):
                raise serializers.ValidationError("控除項目の形式が正しくありません。")
            label = str(row.get("label") or "").strip()
            if not label or len(label) > 20:
                raise serializers.ValidationError("控除の名目を20文字以内で入力してください。")
            try:
                percent = Decimal(str(row.get("percent"))).quantize(Decimal("0.01"))
            except (InvalidOperation, ValueError):
                raise serializers.ValidationError(f"{label}: 割合（%）を数字で入力してください。")
            if not (Decimal("0") < percent <= Decimal("100")):
                raise serializers.ValidationError(f"{label}: 割合は0より大きく100以下で入力してください。")
            code = str(row.get("code") or "").strip()[:20] or secrets.token_hex(4)
            if code in codes:
                code = secrets.token_hex(4)
            codes.add(code)
            out.append({"code": code, "label": label, "percent": str(percent), "default_on": bool(row.get("default_on"))})
        return out

    def validate(self, attrs):
        mode = attrs.get("unlimited_change_mode", getattr(self.instance, "unlimited_change_mode", "unset"))
        price = attrs.get("unlimited_change_price", getattr(self.instance, "unlimited_change_price", None))
        if mode == "fixed" and price is None:
            raise serializers.ValidationError({"unlimited_change_price": "固定額を指定してください。"})
        mode2 = attrs.get("unlimited_change_after_ext_mode", getattr(self.instance, "unlimited_change_after_ext_mode", "unset"))
        price2 = attrs.get("unlimited_change_after_ext_price", getattr(self.instance, "unlimited_change_after_ext_price", None))
        if mode2 == "fixed" and price2 is None:
            raise serializers.ValidationError({"unlimited_change_after_ext_price": "固定額を指定してください。"})
        unit = attrs.get("total_rounding_unit", getattr(self.instance, "total_rounding_unit", 1))
        if unit not in (1, 10, 100, 1000):
            raise serializers.ValidationError({"total_rounding_unit": "1, 10, 100, 1000 のいずれかを指定してください。"})
        return attrs


class CompanyMembershipSerializer(serializers.ModelSerializer):
    user = UserBriefSerializer(read_only=True)

    class Meta:
        model = CompanyMembership
        fields = ["id", "user", "company", "role", "is_active", "created_at"]
        read_only_fields = ["id", "user", "company", "created_at"]


class StoreMembershipSerializer(serializers.ModelSerializer):
    user = UserBriefSerializer(read_only=True)
    store_name = serializers.CharField(source="store.name", read_only=True)

    class Meta:
        model = StoreMembership
        fields = ["id", "user", "store", "store_name", "role", "is_active", "created_at"]
        read_only_fields = ["id", "user", "store_name", "created_at"]


class InvitationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Invitation
        fields = ["id", "company", "email", "display_name", "company_role", "store_roles", "employee", "token", "expires_at", "accepted_at", "created_at"]
        read_only_fields = ["id", "company", "token", "expires_at", "accepted_at", "created_at"]


class AuditLogSerializer(serializers.ModelSerializer):
    user_name = serializers.SerializerMethodField()

    class Meta:
        model = AuditLog
        fields = ["id", "store", "user", "user_name", "action", "target_type", "target_id", "changes", "request_id", "created_at"]

    def get_user_name(self, obj):
        return str(obj.user) if obj.user else ""
