from django.contrib import admin

from .models import IndividualTaxProfile, TaxAuditLog, TaxExport, TaxReturn, TaxReturnVersion, TaxYearConfiguration

admin.site.register(IndividualTaxProfile)
admin.site.register(TaxYearConfiguration)
admin.site.register(TaxReturn)
admin.site.register(TaxReturnVersion)
admin.site.register(TaxExport)


@admin.register(TaxAuditLog)
class TaxAuditLogAdmin(admin.ModelAdmin):
    readonly_fields = [f.name for f in TaxAuditLog._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
