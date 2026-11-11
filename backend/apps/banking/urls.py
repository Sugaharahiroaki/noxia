from django.urls import path

from . import views

urlpatterns = [
    path("banking/accounts", views.AccountsView.as_view()),
    path("banking/accounts/<int:account_id>", views.AccountDetailView.as_view()),
    path("banking/profiles", views.ProfilesView.as_view()),
    path("banking/profiles/<int:profile_id>", views.ProfileDetailView.as_view()),
    path("banking/imports/preview", views.ImportPreviewView.as_view()),
    path("banking/imports", views.ImportsView.as_view()),
    path("banking/imports/<int:import_id>/<str:action>", views.ImportActionView.as_view()),
    path("banking/transactions", views.TransactionsView.as_view()),
    path("banking/transactions/<int:tx_id>/candidates", views.TransactionCandidatesView.as_view()),
    path("banking/transactions/<int:tx_id>/reconcile", views.TransactionReconcileView.as_view()),
    path("banking/transactions/<int:tx_id>/ignore", views.TransactionIgnoreView.as_view()),
    path("banking/transactions/<int:tx_id>/transfer", views.TransactionTransferView.as_view()),
    path("banking/reconciliations/<int:rec_id>/void", views.ReconciliationVoidView.as_view()),
    path("banking/scan", views.ScanView.as_view()),
    path("banking/summary", views.SummaryView.as_view()),
]
