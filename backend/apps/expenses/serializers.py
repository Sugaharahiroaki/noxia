from __future__ import annotations

from rest_framework import serializers

from .models import ExpenseReceipt, ExpenseReceiptEvent, Payable, PayablePayment


def _ocr_payload(r: ExpenseReceipt, *, with_accounts: bool) -> dict:
    conf = r.ocr_confidence or {}
    data = {
        "status": r.ocr_status, "status_label": r.get_ocr_status_display(), "provider": r.ocr_provider, "model": r.ocr_model, "error": r.ocr_error,
        "date": r.ocr_date, "vendor": r.ocr_vendor, "total": str(r.ocr_total) if r.ocr_total is not None else None, "tax_breakdown": r.ocr_tax_breakdown,
        "invoice_number": r.ocr_invoice_number, "summary": r.ocr_summary, "missing_fields": conf.get("missing_fields", []), "warnings": conf.get("warnings", []),
        "is_receipt": conf.get("is_receipt", True), "receipt_count": conf.get("receipt_count", 1),
    }
    if with_accounts:
        data["suggested_accounts"] = conf.get("suggested_accounts", [])
        data["suggested_account_key"] = r.ocr_suggested_account_key
        data["cost_jpy"] = str(r.ocr_cost_jpy)
    return data


class ExpenseReceiptStaffSerializer(serializers.ModelSerializer):
    """店舗タブレット向け。科目・税区分・承認メモ・重複候補の詳細・他店舗の情報は返さない。"""

    status_label = serializers.CharField(source="get_status_display", read_only=True)
    payment_method_label = serializers.CharField(source="get_payment_method_display", read_only=True)
    store_name = serializers.CharField(source="store.name", read_only=True)
    file_name = serializers.CharField(source="evidence.original_name", read_only=True)
    content_type = serializers.CharField(source="evidence.content_type", read_only=True)
    quality_flags = serializers.JSONField(source="evidence.quality_flags", read_only=True)
    ocr = serializers.SerializerMethodField()
    can_edit = serializers.SerializerMethodField()
    has_duplicate_warning = serializers.SerializerMethodField()
    submitted_by_name = serializers.SerializerMethodField()

    class Meta:
        model = ExpenseReceipt
        fields = ["id", "status", "status_label", "payment_method", "payment_method_label", "store", "store_name", "file_name", "content_type", "quality_flags",
                  "receipt_date", "vendor", "total_amount", "tax_breakdown", "invoice_number", "summary", "memo", "ocr", "return_reason", "reject_reason",
                  "submitted_at", "submitted_by_name", "reviewed_at", "version", "can_edit", "has_duplicate_warning", "created_at"]

    def get_ocr(self, r):
        return _ocr_payload(r, with_accounts=False)

    def get_can_edit(self, r):
        return r.status in ("draft", "pending", "returned")

    def get_has_duplicate_warning(self, r):
        return bool(r.duplicate_candidates)

    def get_submitted_by_name(self, r):
        return str(r.submitted_by) if r.submitted_by else ""


class ExpenseReceiptSerializer(ExpenseReceiptStaffSerializer):
    """承認者・経理向け（全項目）。"""

    account_key = serializers.CharField(source="account.key", read_only=True, default=None)
    account_name = serializers.CharField(source="account.name", read_only=True, default=None)
    approved_store_name = serializers.CharField(source="approved_store.name", read_only=True, default=None)
    reviewed_by_name = serializers.SerializerMethodField()
    evidence_id = serializers.IntegerField(source="evidence.id", read_only=True)
    taxpayer_id = serializers.IntegerField(read_only=True)
    sha256 = serializers.CharField(source="evidence.sha256", read_only=True)
    origin = serializers.CharField(source="evidence.origin", read_only=True)

    class Meta(ExpenseReceiptStaffSerializer.Meta):
        fields = ExpenseReceiptStaffSerializer.Meta.fields + [
            "taxpayer_id", "evidence_id", "sha256", "origin", "approved_date", "approved_vendor", "approved_amount", "approved_tax_breakdown", "account",
            "account_key", "account_name", "approved_tax_category", "approved_store", "approved_store_name", "approved_payment_method", "approved_note",
            "duplicate_candidates", "duplicate_override_reason", "cash_movement", "expense", "payable", "reviewed_by_name", "corrected_from",
        ]

    def get_ocr(self, r):
        return _ocr_payload(r, with_accounts=True)

    def get_reviewed_by_name(self, r):
        return str(r.reviewed_by) if r.reviewed_by else ""


class ExpenseReceiptEventSerializer(serializers.ModelSerializer):
    actor_name = serializers.SerializerMethodField()

    class Meta:
        model = ExpenseReceiptEvent
        fields = ["id", "action", "actor_name", "at", "before", "after", "reason"]

    def get_actor_name(self, e):
        return str(e.actor) if e.actor else "システム"


class PayablePaymentSerializer(serializers.ModelSerializer):
    class Meta:
        model = PayablePayment
        fields = ["id", "payable", "date", "amount", "method", "cash_movement", "bank_transaction_ref", "note", "is_voided", "void_reason", "created_at"]
        read_only_fields = fields


class PayableSerializer(serializers.ModelSerializer):
    account_name = serializers.CharField(source="account.name", read_only=True, default=None)
    account_key = serializers.CharField(source="account.key", read_only=True, default=None)
    store_name = serializers.CharField(source="store.name", read_only=True, default=None)
    supplier_name = serializers.CharField(source="supplier.name", read_only=True, default=None)
    balance = serializers.SerializerMethodField()
    payments = PayablePaymentSerializer(many=True, read_only=True)

    class Meta:
        model = Payable
        fields = ["id", "store", "store_name", "supplier", "supplier_name", "counterparty", "invoice_date", "due_date", "amount", "tax_rate", "tax_breakdown",
                  "account", "account_key", "account_name", "description", "evidence", "source", "status", "paid_amount", "balance", "is_voided", "void_reason",
                  "payments", "created_at"]
        read_only_fields = fields

    def get_balance(self, p):
        return str(p.balance)
