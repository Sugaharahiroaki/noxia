from django.urls import path
from rest_framework.routers import SimpleRouter

from . import views

router = SimpleRouter(trailing_slash=False)
router.register("inventory/suppliers", views.SupplierViewSet, basename="supplier")
router.register("inventory/supplier-products", views.SupplierProductViewSet, basename="supplier-product")

urlpatterns = [
    path("inventory/stock", views.StockLevelsView.as_view()),
    path("inventory/movements", views.StockMovementsView.as_view()),
    path("inventory/stock-takes", views.StockTakesView.as_view()),
    path("inventory/reorder-candidates", views.ReorderCandidatesView.as_view()),
    path("inventory/purchase-orders", views.PurchaseOrdersView.as_view()),
    path("inventory/purchase-orders/<int:po_id>", views.PurchaseOrderDetailView.as_view()),
    path("inventory/purchase-orders/<int:po_id>/receive", views.PurchaseOrderReceiveView.as_view()),
    path("inventory/purchase-orders/<int:po_id>/export", views.PurchaseOrderExportView.as_view()),
    path("inventory/purchase-orders/<int:po_id>/<str:action>", views.PurchaseOrderTransitionView.as_view()),
] + router.urls
