from django.urls import path

from . import report_views, views

urlpatterns = [
    path("reports/slips", report_views.SlipListView.as_view()),
    path("reports/slips/<int:visit_id>", report_views.SlipDetailView.as_view()),
    path("analytics/daily", views.DailySalesView.as_view()),
    path("analytics/monthly", views.MonthlySalesView.as_view()),
    path("analytics/breakdown", views.BreakdownView.as_view()),
    path("companies/<int:company_id>/summary", views.CompanySummaryView.as_view()),
    path("exports/<str:kind>.csv", views.ExportView.as_view()),
]
