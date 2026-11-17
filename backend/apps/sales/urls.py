from django.urls import path
from rest_framework.routers import SimpleRouter

from . import views

router = SimpleRouter(trailing_slash=False)
router.register("store/tags", views.TagViewSet, basename="tag")

urlpatterns = router.urls + [
    path("floor/state", views.FloorStateView.as_view()),
    path("visits", views.VisitListView.as_view()),
    path("visits/preview", views.VisitPreviewView.as_view()),
    path("visits/<int:visit_id>", views.VisitDetailView.as_view()),
    path("visits/<int:visit_id>/course-entries", views.VisitCourseEntriesView.as_view()),
    path("visits/<int:visit_id>/course-entries/<int:entry_id>/extend", views.ExtendView.as_view()),
    path("visits/<int:visit_id>/course-entries/<int:entry_id>/change-unlimited", views.ChangeUnlimitedView.as_view()),
    path("visits/<int:visit_id>/course-entries/<int:entry_id>/unlimited-preview", views.UnlimitedPreviewView.as_view()),
    path("visits/<int:visit_id>/course-entries/<int:entry_id>/extend-preview", views.ExtendPreviewView.as_view()),
    path("visits/<int:visit_id>/course-entries/<int:entry_id>/change-time", views.ChangeEntryTimeView.as_view()),
    path("visits/<int:visit_id>/course-entries/<int:entry_id>/extend-options", views.ExtendOptionsView.as_view()),
    path("visits/<int:visit_id>/guests/<int:guest_id>/leave", views.GuestLeaveView.as_view()),
    path("visits/<int:visit_id>/tags", views.VisitTagsView.as_view()),
    path("visits/<int:visit_id>/lines", views.VisitLinesView.as_view()),
    path("visits/<int:visit_id>/lines/batch", views.VisitLinesBatchView.as_view()),
    path("visits/<int:visit_id>/lines/cancel-batch", views.LinesCancelBatchView.as_view()),
    path("visits/<int:visit_id>/lines/<int:line_id>/cancel", views.LineCancelView.as_view()),
    path("visits/<int:visit_id>/lines/<int:line_id>/back-targets", views.LineBackTargetsView.as_view()),
    path("visits/<int:visit_id>/move-table", views.MoveTableView.as_view()),
    path("visits/<int:visit_id>/cancel", views.VisitCancelView.as_view()),
    path("visits/<int:visit_id>/quote", views.VisitQuoteView.as_view()),
    path("visits/<int:visit_id>/checkouts", views.VisitCheckoutsView.as_view()),
    path("checkouts/<int:checkout_id>", views.CheckoutDetailView.as_view()),
    path("checkouts/<int:checkout_id>/handwrite", views.CheckoutHandwriteView.as_view()),
    path("checkouts/<int:checkout_id>/void", views.CheckoutVoidView.as_view()),
    path("checkouts/<int:checkout_id>/delete", views.CheckoutDeleteView.as_view()),
    path("checkouts/<int:checkout_id>/refunds", views.CheckoutRefundsView.as_view()),
    path("checkouts/<int:checkout_id>/receipt-issues", views.CheckoutReceiptIssuesView.as_view()),
    path("receipt-issues/<int:issue_id>/cancel", views.ReceiptIssueCancelView.as_view()),
    path("receivables", views.ReceivableListView.as_view()),
    path("receivables/<int:receivable_id>/collect", views.ReceivableCollectView.as_view()),
    path("sales/history", views.SalesHistoryView.as_view()),
]
