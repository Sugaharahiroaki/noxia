from django.urls import path

from . import views

urlpatterns = [
    path("printing/config", views.PrinterConfigView.as_view()),
    path("printing/test", views.TestPrintView.as_view()),
    path("visits/<int:visit_id>/print/estimate", views.EstimatePrintView.as_view()),
    path("checkouts/<int:checkout_id>/print/receipt", views.ReceiptPrintView.as_view()),
    path("checkouts/<int:checkout_id>/print/ryoshusho", views.RyoshushoPrintView.as_view()),
    path("cash/days/<int:day_id>/print/closing", views.ClosingPrintView.as_view()),
]
