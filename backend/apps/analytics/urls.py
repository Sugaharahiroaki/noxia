from django.urls import path

from . import report_views, views

urlpatterns = [
    path("reports/slips", report_views.SlipListView.as_view()),
    path("reports/slips/<int:visit_id>", report_views.SlipDetailView.as_view()),
    path("reports/monthly", report_views.MonthlyReportView.as_view()),
    path("reports/daily", report_views.DailyReportView.as_view()),
    path("reports/cash", report_views.CashMonthView.as_view()),
    path("reports/cast", report_views.PeopleReportView.as_view()),
    path("reports/staff", report_views.StaffReportView.as_view()),
    path("reports/people/<int:employee_id>", report_views.PersonReportView.as_view()),
    path("reports/people-<str:kind>.csv", report_views.PeopleCsvView.as_view()),
    path("reports/tags", report_views.TagReportView.as_view()),
    path("reports/items", report_views.ItemReportView.as_view()),
    path("reports/customers", report_views.CustomerReportView.as_view()),
    path("reports/export/<str:kind>.csv", report_views.ReportCsvView.as_view()),
    path("analytics/daily", views.DailySalesView.as_view()),
    path("analytics/monthly", views.MonthlySalesView.as_view()),
    path("analytics/breakdown", views.BreakdownView.as_view()),
    path("companies/<int:company_id>/summary", views.CompanySummaryView.as_view()),
    path("exports/<str:kind>.csv", views.ExportView.as_view()),
]
