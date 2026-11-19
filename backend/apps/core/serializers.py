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
    void_pin_set = serializers.SerializerMethodField()

    class Meta:
        model = StoreSettings
        exclude = ["store", "created_at", "updated_at", "void_pin_hash", "void_pin_failed_count", "void_pin_locked_until"]

    def get_void_pin_set(self, obj) -> bool:
        return bool(obj.void_pin_hash)

    def _rate(self, value, label):
        if value is not None and not (Decimal("0") <= value <= Decimal("100")):
            raise serializers.ValidationError(f"{label}は 0〜100 の範囲で入力してください。")
        return value

    def validate_service_charge_rate(self, value):
        return self._rate(value, "サービス料率")

    def validate_service_charge_rate_cash(self, value):
        return self._rate(value, "現金のときのサービス料率")

    def validate_service_charge_rate_card(self, value):
        return self._rate(value, "カードのときのサービス料率")

    def validate_service_charge_rate_receivable(self, value):
        return self._rate(value, "ツケのときのサービス料率")

    def validate_cast_sales_split_mode(self, value):
        if value not in ("equal", "full_each"):
            raise serializers.ValidationError("キャスト売上の分け方は equal か full_each を指定してください。")
        return value

    def validate_nomination_count_mode(self, value):
        if value not in ("per_slip", "per_set"):
            raise serializers.ValidationError("指名回数の数え方は per_slip か per_set を指定してください。")
        return value

    def validate_customer_rank_thresholds(self, value):
        """お客様分析のランク。[{"label": "S", "min_visits": 20}, ...]。回数の多い順に並べ直して保存する。"""
        if not isinstance(value, list):
            raise serializers.ValidationError("ランクの一覧を指定してください。")
        if len(value) > 8:
            raise serializers.ValidationError("ランクは 8 個までです。")
        out, labels, mins = [], set(), set()
        for item in value:
            if not isinstance(item, dict):
                raise serializers.ValidationError("ランクの形式が正しくありません。")
            label = str(item.get("label") or "").strip()
            try:
                n = int(item.get("min_visits"))
            except (TypeError, ValueError):
                raise serializers.ValidationError("来店回数は 0 以上の整数で指定してください。")
            if not label or len(label) > 6:
                raise serializers.ValidationError("ランクの名前は 1〜6 文字で指定してください。")
            if n < 0 or n > 9999:
                raise serializers.ValidationError("来店回数は 0 以上の整数で指定してください。")
            if label in labels or n in mins:
                raise serializers.ValidationError("ランクの名前と来店回数は重ならないようにしてください。")
            labels.add(label)
            mins.add(n)
            out.append({"label": label, "min_visits": n})
        return sorted(out, key=lambda x: -x["min_visits"])

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
        for key in ("tax_rounding_unit", "service_charge_rounding_unit", "total_rounding_unit"):
            unit = attrs.get(key, getattr(self.instance, key, 1))
            if unit not in (1, 10, 100, 1000):
                raise serializers.ValidationError({key: "1, 10, 100, 1000 のいずれかを指定してください。"})
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
        fields = ["id", "user", "store", "store_name", "role", "is_active", "is_tablet", "created_at"]
        read_only_fields = ["id", "user", "store_name", "is_tablet", "created_at"]  # is_tablet は店舗設定の「タブレット」で変える


class InvitationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Invitation
        fields = ["id", "company", "email", "display_name", "company_role", "store_roles", "employee", "token", "expires_at", "accepted_at", "created_at"]
        read_only_fields = ["id", "company", "token", "expires_at", "accepted_at", "created_at"]


class AuditLogSerializer(serializers.ModelSerializer):
    user_name = serializers.SerializerMethodField()
    action_label = serializers.SerializerMethodField()
    target_label = serializers.SerializerMethodField()
    summary = serializers.SerializerMethodField()

    class Meta:
        model = AuditLog
        fields = ["id", "store", "user", "user_name", "action", "action_label", "target_type", "target_label", "target_id", "changes", "summary", "request_id", "created_at"]

    def get_user_name(self, obj):
        return str(obj.user) if obj.user else ""

    def get_action_label(self, obj) -> str:
        from .audit_labels import action_label

        return action_label(obj.action)

    def get_target_label(self, obj) -> str:
        from .audit_labels import target_label

        return target_label(obj.target_type)

    def get_summary(self, obj) -> str:
        from .audit_labels import changes_summary

        return changes_summary(obj.changes)
