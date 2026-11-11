from django.urls import path

from . import views

urlpatterns = [
    path("closing/years/<int:year>/checklist", views.YearChecklistView.as_view()),
    path("closing/years/<int:year>/pl", views.YearPlView.as_view()),
    path("closing/years/<int:year>/pl.xlsx", views.YearPlView.as_view(), {"ext": "xlsx"}),
    path("closing/years/<int:year>/pl.pdf", views.YearPlView.as_view(), {"ext": "pdf"}),
    path("closing/inventory", views.InventoryView.as_view()),
    path("closing/inventory/<int:closing_id>", views.InventoryDetailView.as_view()),
    path("closing/inventory/<int:closing_id>/<str:action>", views.InventoryActionView.as_view()),
    path("closing/fixed-assets", views.FixedAssetsView.as_view()),
    path("closing/fixed-assets/<int:asset_id>", views.FixedAssetDetailView.as_view()),
    path("closing/fixed-assets/<int:asset_id>/dispose", views.FixedAssetDisposeView.as_view()),
    path("closing/depreciation", views.DepreciationView.as_view()),
    path("closing/depreciation/compute", views.DepreciationComputeView.as_view()),
    path("closing/adjustments", views.AdjustmentsView.as_view()),
    path("closing/adjustments/<int:adjustment_id>/void", views.AdjustmentVoidView.as_view()),
]
