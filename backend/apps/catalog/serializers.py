from __future__ import annotations

from rest_framework import serializers

from apps.core.api import StorePK

from .models import Category, Course, CourseExtensionOption, Product, Table


class ExtensionOptionSerializer(serializers.ModelSerializer):
    class Meta:
        model = CourseExtensionOption
        fields = ["id", "minutes", "price", "sort_order"]
        read_only_fields = ["id"]

    def validate(self, attrs):
        if attrs.get("minutes") is not None and attrs["minutes"] < 1:
            raise serializers.ValidationError({"minutes": "延長の分数は1以上で入力してください。"})
        if attrs.get("price") is not None and attrs["price"] < 0:
            raise serializers.ValidationError({"price": "延長料金は0以上で入力してください。"})
        return attrs


class CourseSerializer(serializers.ModelSerializer):
    is_unlimited = serializers.BooleanField(read_only=True)
    # 延長の候補。渡したときは丸ごと入れ替える（渡さなければ変えない）
    extension_options = ExtensionOptionSerializer(many=True, required=False)

    def _save_options(self, course, options):
        if options is None:
            return
        course.extension_options.all().delete()
        for i, o in enumerate(options):
            CourseExtensionOption.objects.create(course=course, minutes=o["minutes"], price=o["price"], sort_order=o.get("sort_order", i))

    def create(self, validated_data):
        options = validated_data.pop("extension_options", None)
        course = super().create(validated_data)
        self._save_options(course, options)
        return course

    def update(self, instance, validated_data):
        options = validated_data.pop("extension_options", None)
        course = super().update(instance, validated_data)
        self._save_options(course, options)
        return course

    class Meta:
        model = Course
        fields = ["id", "name", "price", "duration_minutes", "extension_price", "extension_minutes", "tax_rate",
                  "is_active", "sort_order", "note", "is_unlimited", "kind", "extension_options"]
        read_only_fields = ["id"]

    def validate(self, attrs):
        if attrs.get("price") is not None and attrs["price"] < 0:
            raise serializers.ValidationError({"price": "料金は0以上で入力してください。"})
        return attrs


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ["id", "name", "kind", "sort_order", "is_active"]
        read_only_fields = ["id"]


class ProductSerializer(serializers.ModelSerializer):
    category = StorePK(model=Category)
    category_name = serializers.CharField(source="category.name", read_only=True)
    category_kind = serializers.CharField(source="category.kind", read_only=True)

    class Meta:
        model = Product
        fields = ["id", "category", "category_name", "category_kind", "name", "price", "tax_rate", "is_active", "sort_order",
                  "stock_mode", "volume_ml", "shot_ml", "reorder_point", "min_stock", "cost_price", "is_bottle_keepable", "note"]
        read_only_fields = ["id"]

    def validate(self, attrs):
        mode = attrs.get("stock_mode", getattr(self.instance, "stock_mode", "none"))
        if mode == "shot":
            vol = attrs.get("volume_ml", getattr(self.instance, "volume_ml", None))
            shot = attrs.get("shot_ml", getattr(self.instance, "shot_ml", None))
            if not vol or not shot:
                raise serializers.ValidationError("ショット販売の商品は容量(ml)と1杯量(ml)を設定してください。")
        if attrs.get("price") is not None and attrs["price"] < 0:
            raise serializers.ValidationError({"price": "価格は0以上で入力してください。"})
        return attrs


class TableSerializer(serializers.ModelSerializer):
    class Meta:
        model = Table
        fields = ["id", "name", "capacity", "sort_order", "is_active", "color", "kind"]
        read_only_fields = ["id"]

    def validate_color(self, value):
        import re

        value = (value or "").strip()
        if value and not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
            raise serializers.ValidationError("色は #RRGGBB の形式で指定してください。")
        return value
