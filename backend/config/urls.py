from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("django-admin/", admin.site.urls),
    path("api/", include("apps.core.urls")),
    path("api/", include("apps.catalog.urls")),
    path("api/", include("apps.staff.urls")),
    path("api/", include("apps.sales.urls")),
    path("api/", include("apps.cash.urls")),
    path("api/", include("apps.customers.urls")),
    path("api/", include("apps.inventory.urls")),
    path("api/", include("apps.line_integration.urls")),
    path("api/", include("apps.terminals.urls")),
    path("api/", include("apps.analytics.urls")),
    path("api/", include("apps.accounting.urls")),
    path("api/", include("apps.expenses.urls")),
    path("api/", include("apps.banking.urls")),
    path("api/", include("apps.closing.urls")),
    path("api/", include("apps.tax.urls")),
]
