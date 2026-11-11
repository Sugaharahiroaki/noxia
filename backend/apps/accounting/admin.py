from django.contrib import admin

from .models import TaxPayer, WithholdingRule


@admin.register(WithholdingRule)
class WithholdingRuleAdmin(admin.ModelAdmin):
    """税制改正時は新しい行を追加する（過去の行は変更しない。計算済みの明細はルールの版を保存している）。"""

    list_display = ("category", "version", "effective_from", "effective_to", "rate_percent", "daily_deduction", "is_active")


@admin.register(TaxPayer)
class TaxPayerAdmin(admin.ModelAdmin):
    list_display = ("name", "company", "entity_type", "tax_office_name", "payroll_due_special", "is_active")
