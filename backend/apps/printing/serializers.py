from __future__ import annotations

import re

from rest_framework import serializers

from .models import PrinterConfig

INVOICE_RE = re.compile(r"^T\d{13}$")


class PrinterConfigSerializer(serializers.ModelSerializer):
    shop_name_default = serializers.SerializerMethodField()

    class Meta:
        model = PrinterConfig
        fields = [
            "enabled", "printer_name", "model_name", "connection", "paper_width",
            "print_estimate", "print_receipt", "print_ryoshusho", "print_closing",
            "shop_name", "shop_name_default", "address1", "address2", "address3", "phone", "invoice_number", "receipt_message",
            "ryoshusho_proviso", "stamp_box_from", "updated_at",
        ]
        read_only_fields = ["updated_at", "connection"]

    def get_shop_name_default(self, obj):
        return obj.store.name

    def validate_paper_width(self, value):
        if value not in (80, 58):
            raise serializers.ValidationError("用紙の幅は 80mm か 58mm を選んでください。")
        return value

    def validate_invoice_number(self, value):
        value = (value or "").strip().upper()
        if value and not INVOICE_RE.match(value):
            raise serializers.ValidationError("登録番号は T と 13 桁の数字です（例: T1234567890123）。")
        return value

    def validate_stamp_box_from(self, value):
        if value is not None and value < 0:
            raise serializers.ValidationError("0 円以上で入れてください。")
        return value

    def validate_printer_name(self, value):
        value = (value or "").strip()
        if not value:
            raise serializers.ValidationError("プリンター名を入れてください。")
        return value
