from django.urls import path
from django.views.decorators.csrf import csrf_exempt

from . import views

urlpatterns = [
    path("terminals/config", views.TerminalConfigView.as_view()),
    path("terminals/devices", views.TerminalDevicesView.as_view()),
    path("terminals/devices/<int:terminal_id>", views.TerminalDeviceDetailView.as_view()),
    path("terminals/devices/<int:terminal_id>/pair", views.TerminalPairView.as_view()),
    path("terminals/status", views.TerminalStatusView.as_view()),
    path("terminals/webhook/<str:token>", csrf_exempt(views.SquareWebhookView.as_view())),
    path("visits/<int:visit_id>/terminal-payments", views.VisitTerminalPaymentsView.as_view()),
    path("terminal-payments/<int:tp_id>", views.TerminalPaymentDetailView.as_view()),
    path("terminal-payments/<int:tp_id>/cancel", views.TerminalPaymentCancelView.as_view()),
    path("terminal-payments/<int:tp_id>/simulate", views.TerminalPaymentSimulateView.as_view()),
    path("terminal-payments/<int:tp_id>/resolve", views.TerminalPaymentResolveView.as_view()),
]
