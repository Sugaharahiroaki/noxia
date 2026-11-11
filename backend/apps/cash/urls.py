from django.urls import path

from . import views

urlpatterns = [
    path("cash/current", views.CurrentBusinessDayView.as_view()),
    path("cash/open", views.OpenBusinessDayView.as_view()),
    path("cash/close", views.CloseBusinessDayView.as_view()),
    path("cash/movements", views.CashMovementsView.as_view()),
    path("cash/movements/<int:movement_id>/void", views.CashMovementVoidView.as_view()),
    path("cash/days", views.BusinessDayListView.as_view()),
    path("cash/days/<int:day_id>", views.BusinessDayDetailView.as_view()),
    path("cash/days/<int:day_id>/revise", views.BusinessDayReviseView.as_view()),
    path("cash/days/<int:day_id>/reopen", views.ReopenBusinessDayView.as_view()),
    path("cash/days/<int:day_id>/bank-deposits", views.BankDepositsView.as_view()),
]
