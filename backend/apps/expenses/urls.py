from django.urls import path

from . import views

urlpatterns = [
    path("expenses/receipts", views.ReceiptsView.as_view()),
    path("expenses/receipts/pending-count", views.PendingCountView.as_view()),
    path("expenses/receipts/duplicates", views.DuplicatesView.as_view()),
    path("expenses/receipts/<int:receipt_id>", views.ReceiptDetailView.as_view()),
    path("expenses/receipts/<int:receipt_id>/file", views.ReceiptFileView.as_view()),
    path("expenses/receipts/<int:receipt_id>/events", views.ReceiptEventsView.as_view()),
    path("expenses/receipts/<int:receipt_id>/ocr/retry", views.ReceiptOcrRetryView.as_view()),
    path("expenses/receipts/<int:receipt_id>/<str:action>", views.ReceiptActionView.as_view()),
    path("expenses/register-check", views.RegisterCheckView.as_view()),
    path("expenses/payables", views.PayablesView.as_view()),
    path("expenses/payables/<int:payable_id>", views.PayableDetailView.as_view()),
    path("expenses/payables/<int:payable_id>/void", views.PayableVoidView.as_view()),
    path("expenses/payables/<int:payable_id>/payments", views.PayablePaymentsView.as_view()),
    path("expenses/payables/<int:payable_id>/payments/<int:payment_id>/void", views.PayablePaymentVoidView.as_view()),
    path("expenses/settings/ai", views.AiSettingsView.as_view()),
]
