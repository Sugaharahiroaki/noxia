from django.contrib import admin

from .models import BankAccount, BankImportProfile, BankReconciliation, BankStatementImport, BankTransaction


@admin.register(BankAccount)
class BankAccountAdmin(admin.ModelAdmin):
    list_display = ("name", "taxpayer", "bank_name", "account_number_last4", "is_active")


@admin.register(BankStatementImport)
class BankStatementImportAdmin(admin.ModelAdmin):
    list_display = ("id", "bank_account", "original_name", "status", "rows_imported", "balance_check", "created_at")
    readonly_fields = ("sha256", "file")


@admin.register(BankTransaction)
class BankTransactionAdmin(admin.ModelAdmin):
    list_display = ("date", "bank_account", "description", "amount_in", "amount_out", "status")
    list_filter = ("status",)


admin.site.register(BankImportProfile)
admin.site.register(BankReconciliation)
