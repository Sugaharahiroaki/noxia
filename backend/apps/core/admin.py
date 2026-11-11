from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import AuditLog, BackgroundJob, Company, CompanyMembership, CompanySettings, Invitation, Store, StoreMembership, StoreSettings, User

admin.site.register(User, UserAdmin)
admin.site.register(Company)
admin.site.register(Store)
admin.site.register(StoreSettings)
admin.site.register(CompanySettings)
admin.site.register(CompanyMembership)
admin.site.register(StoreMembership)
admin.site.register(Invitation)
admin.site.register(BackgroundJob)


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    """監査ログは追記専用。管理画面からは閲覧だけ。"""

    list_display = ("created_at", "action", "user", "company", "store", "target_type", "target_id")
    list_filter = ("action",)
    search_fields = ("action", "target_id", "request_id")
    readonly_fields = [f.name for f in AuditLog._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
