from django.urls import path
from rest_framework.routers import SimpleRouter

from . import views

router = SimpleRouter(trailing_slash=False)
router.register("customers", views.CustomerViewSet, basename="customer")
router.register("bottle-keeps", views.BottleKeepViewSet, basename="bottle-keep")
router.register("reservations", views.ReservationViewSet, basename="reservation")

urlpatterns = [
    path("customers/<int:customer_id>/history", views.CustomerHistoryView.as_view()),
    path("reservations/<int:reservation_id>/seat", views.ReservationSeatView.as_view()),
] + router.urls
