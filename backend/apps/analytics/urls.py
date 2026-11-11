from django.urls import path

from . import views

urlpatterns = [
    path("analytics/daily", views.DailySalesView.as_view()),
    path("analytics/monthly", views.MonthlySalesView.as_view()),
    path("analytics/breakdown", views.BreakdownView.as_view()),
    path("companies/<int:company_id>/summary", views.CompanySummaryView.as_view()),
    path("exports/<str:kind>.csv", views.ExportView.as_view()),
]
