from django.contrib import admin

from .models import ExpenseReceipt, ExpenseReceiptEvent, Payable, PayablePayment


@admin.register(ExpenseReceipt)
class ExpenseReceiptAdmin(admin.ModelAdmin):
    list_display = ("id", "store", "status", "payment_method", "receipt_date", "total_amount", "vendor", "ocr_status", "created_at")
    list_filter = ("status", "payment_method", "ocr_status")
    search_fields = ("vendor", "approved_vendor", "memo")
    readonly_fields = [f.name for f in ExpenseReceipt._meta.fields]


@admin.register(ExpenseReceiptEvent)
class ExpenseReceiptEventAdmin(admin.ModelAdmin):
    list_display = ("id", "receipt", "action", "actor", "at")
    readonly_fields = [f.name for f in ExpenseReceiptEvent._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


admin.site.register(Payable)
admin.site.register(PayablePayment)
