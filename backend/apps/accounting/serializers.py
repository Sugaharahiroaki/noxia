from __future__ import annotations

from rest_framework import serializers

from apps.core.api import StorePK
from apps.staff.models import Employee

from .models import (
    AccountingAccessGrant, CardSettlement, CastContract, Evidence, Expense, Journal, OwnerFundTransfer, SalaryWithholdingEntry, TaxPayer,
    WithholdingAdjustment, WithholdingRule,
)


class TaxPayerSerializer(serializers.ModelSerializer):
    entity_type_label = serializers.CharField(source="get_entity_type_display", read_only=True)
    store_ids = serializers.SerializerMethodField()

    class Meta:
        model = TaxPayer
        fields = ["id", "name", "entity_type", "entity_type_label", "corporate_number", "representative_name", "address", "phone",
                  "tax_office_name", "reference_number", "payroll_due_special", "fiscal_year_end_month", "filing_method", "is_active", "note", "store_ids"]
        read_only_fields = ["id"]

    def get_store_ids(self, obj):
        return list(obj.store_profiles.values_list("store_id", flat=True))

    def validate_corporate_number(self, v):
        if v and (not v.isdigit() or len(v) != 13):
            raise serializers.ValidationError("法人番号は13桁の数字で入力してください。")
        return v

    def validate_fiscal_year_end_month(self, v):
        if not 1 <= v <= 12:
            raise serializers.ValidationError("決算月は1〜12で入力してください。")
        return v


class GrantSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source="user.username", read_only=True)
    display_name = serializers.CharField(source="user.display_name", read_only=True)
    role_label = serializers.CharField(source="get_role_display", read_only=True)

    class Meta:
        model = AccountingAccessGrant
        fields = ["id", "user", "username", "display_name", "role", "role_label", "is_active", "expires_on", "note", "created_at"]
        read_only_fields = ["id", "user", "created_at"]


DEDUCTION_CALCS = ("percent", "per_day", "per_payment")


class CastContractSerializer(serializers.ModelSerializer):
    employee = StorePK(model=Employee, scope="company")
    employee_name = serializers.CharField(source="employee.display_name", read_only=True)
    contract_type_label = serializers.CharField(source="get_contract_type_display", read_only=True)
    tax_category_label = serializers.CharField(source="get_tax_category_display", read_only=True)
    tax_review_status_label = serializers.CharField(source="get_tax_review_status_display", read_only=True)
    calc_period_label = serializers.CharField(source="get_calc_period_display", read_only=True)
    tax_reviewed_by_name = serializers.SerializerMethodField()
    has_document = serializers.SerializerMethodField()

    class Meta:
        model = CastContract
        fields = ["id", "employee", "employee_name", "contract_type", "contract_type_label", "start_date", "end_date", "calc_period", "calc_period_label",
                  "payment_method", "payment_note", "tax_category", "tax_category_label", "tax_review_status", "tax_review_status_label",
                  "tax_reviewed_by_name", "tax_reviewed_at", "review_checklist", "review_note", "advisor_note", "advisor_noted_at", "deductions",
                  "document_name", "has_document", "is_active", "created_at"]
        read_only_fields = ["id", "tax_category", "tax_review_status", "tax_reviewed_at", "review_checklist", "review_note", "advisor_note",
                            "advisor_noted_at", "document_name", "created_at"]

    def get_tax_reviewed_by_name(self, obj):
        return str(obj.tax_reviewed_by) if obj.tax_reviewed_by else None

    def get_has_document(self, obj):
        return bool(obj.document)

    def validate(self, attrs):
        start = attrs.get("start_date", getattr(self.instance, "start_date", None))
        end = attrs.get("end_date", getattr(self.instance, "end_date", None))
        if start and end and end < start:
            raise serializers.ValidationError({"end_date": "契約終了日は開始日以降にしてください。"})
        return attrs

    def validate_deductions(self, value):
        if not isinstance(value, list):
            raise serializers.ValidationError("控除の形式が正しくありません。")
        out = []
        for d in value:
            label = str(d.get("label") or "").strip()
            calc = d.get("calc")
            if not label or calc not in DEDUCTION_CALCS:
                raise serializers.ValidationError("控除は名前と計算方法（percent / per_day / per_payment）を指定してください。")
            if "インボイス" in label or "消費税" in label:
                # 法令上の根拠がない一律の「インボイス税」等の控除は登録させない（取引条件の変更は契約で個別に合意する）
                raise serializers.ValidationError("インボイス・消費税を名目にした一律の控除は登録できません。取引条件は契約書で個別に定めてください。")
            try:
                value_d = float(d.get("value"))
            except (TypeError, ValueError):
                raise serializers.ValidationError("控除の値は数値で入力してください。")
            if value_d < 0 or (calc == "percent" and value_d > 100):
                raise serializers.ValidationError("控除の値が範囲外です。")
            out.append({"code": str(d.get("code") or label)[:40], "label": label[:40], "calc": calc, "value": str(d.get("value"))})
        return out


class WithholdingRuleSerializer(serializers.ModelSerializer):
    class Meta:
        model = WithholdingRule
        fields = ["id", "category", "version", "effective_from", "effective_to", "rate_percent", "components", "daily_deduction", "rounding",
                  "legal_basis", "source_url", "is_active"]


class SalaryEntrySerializer(serializers.ModelSerializer):
    employee = StorePK(model=Employee, scope="company", required=False, allow_null=True)
    employee_name = serializers.CharField(source="employee.display_name", read_only=True, default=None)
    kind_label = serializers.CharField(source="get_kind_display", read_only=True)

    class Meta:
        model = SalaryWithholdingEntry
        fields = ["id", "employee", "employee_name", "kind", "kind_label", "paid_on", "period_start", "period_end", "gross_amount", "withholding_amount",
                  "headcount", "source", "note", "is_voided", "void_reason", "created_at"]
        read_only_fields = ["id", "source", "is_voided", "void_reason", "created_at"]

    def validate(self, attrs):
        if attrs.get("gross_amount") is not None and attrs["gross_amount"] < 0:
            raise serializers.ValidationError({"gross_amount": "0以上で入力してください。"})
        if attrs.get("withholding_amount") is not None and attrs["withholding_amount"] < 0:
            raise serializers.ValidationError({"withholding_amount": "0以上で入力してください。"})
        ps, pe = attrs.get("period_start"), attrs.get("period_end")
        if bool(ps) != bool(pe) or (ps and pe and pe < ps):
            raise serializers.ValidationError({"period_end": "計算期間は開始日と終了日を両方、正しい順で入力してください。"})
        return attrs


class AdjustmentSerializer(serializers.ModelSerializer):
    employee = StorePK(model=Employee, scope="company", required=False, allow_null=True)
    kind_label = serializers.CharField(source="get_kind_display", read_only=True)

    class Meta:
        model = WithholdingAdjustment
        fields = ["id", "employee", "record", "category", "kind", "kind_label", "applies_on", "target_delta", "deduction_delta", "amount_delta",
                  "reason", "is_voided", "void_reason", "created_at"]
        read_only_fields = ["id", "record", "is_voided", "void_reason", "created_at"]

    def validate_reason(self, v):
        if not v.strip():
            raise serializers.ValidationError("訂正の理由を入力してください。")
        return v


class EvidenceSerializer(serializers.ModelSerializer):
    kind_label = serializers.CharField(source="get_kind_display", read_only=True)
    uploaded_by_name = serializers.SerializerMethodField()

    class Meta:
        model = Evidence
        fields = ["id", "kind", "kind_label", "original_name", "content_type", "size", "sha256", "transaction_date", "amount", "counterparty",
                  "description", "retention_until", "store", "origin", "phash", "captured_at", "page_count", "image_width", "image_height", "quality_flags",
                  "is_deleted", "deleted_reason", "deleted_at", "uploaded_by_name", "created_at"]

    def get_uploaded_by_name(self, obj):
        return str(obj.uploaded_by) if obj.uploaded_by else ""


def validate_tax_breakdown(value, total=None):
    """[{"rate": "8"|"10"|"0", "taxable_incl": "1080", "tax": "80"}] の検証。合計が total と違えば警告だけ返す（拒否しない）。"""
    from decimal import Decimal, InvalidOperation

    if value in (None, ""):
        return []
    if not isinstance(value, list):
        raise serializers.ValidationError("税率内訳の形式が正しくありません。")
    out = []
    for row in value:
        try:
            rate = Decimal(str(row.get("rate")))
            amt = Decimal(str(row.get("taxable_incl") if row.get("taxable_incl") is not None else row.get("amount")))
            tax = Decimal(str(row.get("tax"))) if row.get("tax") not in (None, "") else None
        except (InvalidOperation, TypeError, AttributeError):
            raise serializers.ValidationError("税率内訳の金額が数値ではありません。")
        if rate not in (Decimal(0), Decimal(8), Decimal(10)):
            raise serializers.ValidationError("税率は 10 / 8 / 0 から選んでください。")
        if amt <= 0:
            raise serializers.ValidationError("税率内訳の金額は1円以上で入力してください。")
        out.append({"rate": str(rate.quantize(Decimal(1))), "taxable_incl": str(amt.quantize(Decimal(1))), "tax": str(tax.quantize(Decimal(1))) if tax is not None else None})
    return out


class ExpenseSerializer(serializers.ModelSerializer):
    paid_from_label = serializers.CharField(source="get_paid_from_display", read_only=True)
    account_name = serializers.CharField(source="account.name", read_only=True, default=None)

    class Meta:
        model = Expense
        fields = ["id", "store", "date", "amount", "tax_rate", "tax_breakdown", "account_key", "account", "account_name", "counterparty", "invoice_number",
                  "description", "paid_from", "paid_from_label", "evidence", "is_voided", "void_reason", "corrected_from", "created_at"]
        read_only_fields = ["id", "is_voided", "void_reason", "corrected_from", "created_at", "account"]

    def validate_tax_breakdown(self, value):
        return validate_tax_breakdown(value)

    def validate(self, attrs):
        from .journals import EXPENSE_KEYS

        if attrs.get("amount") is not None and attrs["amount"] <= 0:
            raise serializers.ValidationError({"amount": "1円以上で入力してください。"})
        if attrs.get("account_key") and attrs["account_key"] not in EXPENSE_KEYS:
            raise serializers.ValidationError({"account_key": "経費の勘定科目を選んでください。"})
        if attrs.get("tax_rate") is not None and attrs["tax_rate"] not in (0, 8, 10):
            raise serializers.ValidationError({"tax_rate": "税率は 10 / 8 / 0 から選んでください。"})
        num = attrs.get("invoice_number") or ""
        if num and not (len(num) == 14 and num[0] == "T" and num[1:].isdigit()):
            raise serializers.ValidationError({"invoice_number": "登録番号は T＋13桁の数字です。"})
        bd = attrs.get("tax_breakdown")
        amount = attrs.get("amount", getattr(self.instance, "amount", None))
        if bd and amount is not None:
            from decimal import Decimal

            total = sum(Decimal(r["taxable_incl"]) for r in bd)
            if total > amount:
                raise serializers.ValidationError({"tax_breakdown": "税率内訳の合計が金額を超えています。"})
        return attrs


class OwnerFundTransferSerializer(serializers.ModelSerializer):
    class Meta:
        model = OwnerFundTransfer
        fields = ["id", "store", "date", "direction", "amount", "method", "cash_movement", "note", "is_voided", "void_reason", "created_at"]
        read_only_fields = ["id", "cash_movement", "is_voided", "void_reason", "created_at"]

    def validate(self, attrs):
        if attrs.get("direction") not in ("in", "out"):
            raise serializers.ValidationError({"direction": "投入（in）か引出し（out）を選んでください。"})
        if attrs.get("method") not in ("bank", "cash_register"):
            raise serializers.ValidationError({"method": "口座（bank）かレジ現金（cash_register）を選んでください。"})
        if attrs.get("amount") is not None and attrs["amount"] <= 0:
            raise serializers.ValidationError({"amount": "1円以上で入力してください。"})
        return attrs


class CardSettlementSerializer(serializers.ModelSerializer):
    class Meta:
        model = CardSettlement
        fields = ["id", "store", "deposit_date", "sales_amount", "fee_amount", "deposit_amount", "provider_name", "note", "is_voided", "void_reason", "created_at"]
        read_only_fields = ["id", "is_voided", "void_reason", "created_at"]

    def validate(self, attrs):
        if attrs["sales_amount"] - attrs.get("fee_amount", 0) != attrs["deposit_amount"]:
            raise serializers.ValidationError({"deposit_amount": "入金額 ＝ 対象の売上額 − 手数料 になるように入力してください。"})
        return attrs


class JournalSerializer(serializers.ModelSerializer):
    sync_status = serializers.CharField(source="freee_sync.status", read_only=True)
    sync_status_label = serializers.CharField(source="freee_sync.get_status_display", read_only=True)
    freee_id = serializers.IntegerField(source="freee_sync.freee_id", read_only=True)
    sync_id = serializers.IntegerField(source="freee_sync.id", read_only=True)
    last_error = serializers.CharField(source="freee_sync.last_error", read_only=True)
    store_name = serializers.CharField(source="store.name", read_only=True, default=None)

    class Meta:
        model = Journal
        fields = ["id", "sync_key", "source_type", "source_id", "store_name", "issue_date", "description", "lines", "warnings", "revision", "is_void",
                  "sync_id", "sync_status", "sync_status_label", "freee_id", "last_error", "updated_at"]
