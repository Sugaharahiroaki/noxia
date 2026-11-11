from django.urls import path
from rest_framework.routers import SimpleRouter

from . import views

router = SimpleRouter(trailing_slash=False)
router.register("staff/back-rules", views.BackRuleViewSet, basename="back-rule")

urlpatterns = [
    path("staff/employees", views.EmployeesView.as_view()),
    path("staff/employees/<int:employee_id>", views.EmployeeDetailView.as_view()),
    path("staff/employees/<int:employee_id>/assignment", views.EmployeeAssignmentView.as_view()),
    path("staff/employees/<int:employee_id>/login-account", views.EmployeeLoginAccountView.as_view()),
    path("staff/employees/<int:employee_id>/wages", views.EmployeeWagesView.as_view()),
    path("staff/backs", views.BackEntriesView.as_view()),
    path("staff/backs/adjust", views.BackAdjustmentView.as_view()),
    path("staff/shifts", views.ShiftsView.as_view()),
    path("staff/shifts/clock-in", views.ClockInView.as_view()),
    path("staff/shifts/<int:shift_id>/clock-out", views.ClockOutView.as_view()),
    path("staff/shifts/<int:shift_id>/break", views.BreakView.as_view()),
    path("staff/shifts/<int:shift_id>/correct", views.ShiftCorrectView.as_view()),
    path("staff/payments", views.EmployeePaymentsView.as_view()),
    path("staff/daily-payouts", views.DailyPayoutsView.as_view()),
    path("staff/daily-payouts/pay", views.DailyPayoutPayView.as_view()),
    path("staff/statements", views.StatementsView.as_view()),
    path("staff/pay-periods", views.PayPeriodsView.as_view()),
    path("staff/pay-periods/<int:period_id>", views.PayPeriodDetailView.as_view()),
    path("staff/pay-periods/<int:period_id>/close", views.PayPeriodCloseView.as_view()),
    path("staff/me", views.MyStaffView.as_view()),
] + router.urls
