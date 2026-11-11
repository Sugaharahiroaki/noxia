from django.urls import path
from django.views.decorators.csrf import csrf_exempt
from rest_framework.routers import SimpleRouter

from . import views

router = SimpleRouter(trailing_slash=False)
router.register("line/users", views.LineUserViewSet, basename="line-user")
router.register("line/templates", views.TemplateViewSet, basename="line-template")
router.register("line/drafts", views.DraftViewSet, basename="line-draft")
router.register("line/inquiries", views.InquiryViewSet, basename="line-inquiry")

urlpatterns = [
    path("line/integration", views.IntegrationView.as_view()),
    path("line/drafts/<int:draft_id>/review", views.DraftReviewView.as_view()),
    path("line/drafts/<int:draft_id>/send", views.DraftSendView.as_view()),
    path("line/webhook-events", views.WebhookEventsView.as_view()),
    path("line/webhook/<str:token>", csrf_exempt(views.WebhookView.as_view())),
] + router.urls
