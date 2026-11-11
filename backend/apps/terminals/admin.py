from django.contrib import admin

from .models import CardTerminal, TerminalPayment


@admin.register(CardTerminal)
class CardTerminalAdmin(admin.ModelAdmin):
    list_display = ("id", "store", "name", "provider", "status", "is_active")
    list_filter = ("provider", "status")


@admin.register(TerminalPayment)
class TerminalPaymentAdmin(admin.ModelAdmin):
    list_display = ("id", "store", "visit", "terminal", "amount", "status", "created_at")
    list_filter = ("status", "provider")
    readonly_fields = [f.name for f in TerminalPayment._meta.fields]
