from __future__ import annotations

from rest_framework import serializers

from .models import BankAccount, BankImportProfile, BankReconciliation, BankStatementImport, BankTransaction


class BankAccountSerializer(serializers.ModelSerializer):
    account_type_label = serializers.CharField(source="get_account_type_display", read_only=True)

    class Meta:
        model = BankAccount
        fields = ["id", "name", "bank_name", "bank_code", "branch_name", "branch_code", "account_type", "account_type_label", "account_number_last4", "holder_name",
                  "opening_balance", "opening_balance_date", "is_business", "is_active", "note", "created_at"]
        read_only_fields = ["id", "created_at"]

    def validate_bank_code(self, v):
        if v and (not v.isdigit() or len(v) != 4):
            raise serializers.ValidationError("銀行コードは4桁の数字です。")
        return v

    def validate_branch_code(self, v):
        if v and (not v.isdigit() or len(v) != 3):
            raise serializers.ValidationError("支店コードは3桁の数字です。")
        return v

    def validate_account_number_last4(self, v):
        v = (v or "").strip()
        if v and (not v.isdigit() or len(v) > 4):
            raise serializers.ValidationError("口座番号は下4桁だけ入力してください（全桁は保存しません）。")
        return v[-4:]


class BankImportProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = BankImportProfile
        fields = ["id", "bank_account", "name", "encoding", "delimiter", "header_rows", "footer_rows", "column_map", "date_format", "amount_rules", "last_used_at", "created_at"]
        read_only_fields = ["id", "last_used_at", "created_at"]

    def validate_column_map(self, v):
        if not isinstance(v, dict):
            raise serializers.ValidationError("列の対応の形式が正しくありません。")
        out = {}
        for k, val in v.items():
            if val in (None, ""):
                out[k] = None
                continue
            try:
                out[k] = int(val)
            except (TypeError, ValueError):
                raise serializers.ValidationError(f"{k} の列番号が不正です。")
        if out.get("date") is None:
            raise serializers.ValidationError("日付の列を指定してください。")
        if out.get("withdrawal") is None and out.get("deposit") is None and out.get("amount_signed") is None:
            raise serializers.ValidationError("入金・出金（または金額）の列を指定してください。")
        return out


class BankStatementImportSerializer(serializers.ModelSerializer):
    bank_account_name = serializers.CharField(source="bank_account.name", read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    imported_by_name = serializers.SerializerMethodField()

    class Meta:
        model = BankStatementImport
        fields = ["id", "bank_account", "bank_account_name", "profile", "original_name", "sha256", "encoding_detected", "column_map", "rows_total", "rows_imported",
                  "rows_duplicate", "rows_error", "period_start", "period_end", "opening_balance", "closing_balance", "balance_check", "balance_note", "status", "status_label",
                  "error_log", "imported_by_name", "committed_at", "rolled_back_at", "created_at"]

    def get_imported_by_name(self, obj):
        return str(obj.imported_by) if obj.imported_by else ""


class BankReconciliationSerializer(serializers.ModelSerializer):
    match_type_label = serializers.CharField(source="get_match_type_display", read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    target_id = serializers.ReadOnlyField()
    store_name = serializers.CharField(source="store.name", read_only=True, default=None)
    counter_account = serializers.CharField(source="counter_transaction.bank_account.name", read_only=True, default=None)

    class Meta:
        model = BankReconciliation
        fields = ["id", "bank_transaction", "match_type", "match_type_label", "target_id", "card_settlement", "bank_deposit", "employee_payment", "expense", "payable_payment_ref",
                  "owner_transfer", "tax_remittance", "counter_transaction", "counter_account", "journal", "amount", "fee_amount", "status", "status_label", "rule", "score", "label",
                  "period_start", "period_end", "store", "store_name", "confirmed_at", "voided_at", "void_reason", "created_at"]


class BankTransactionSerializer(serializers.ModelSerializer):
    bank_account_name = serializers.CharField(source="bank_account.name", read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    reconciliations = BankReconciliationSerializer(many=True, read_only=True)

    class Meta:
        model = BankTransaction
        fields = ["id", "bank_account", "bank_account_name", "statement_import", "row_no", "date", "description", "counterparty", "amount_in", "amount_out", "balance",
                  "status", "status_label", "ignore_reason", "reconciliations", "raw"]
