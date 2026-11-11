from django.urls import path

from . import views

urlpatterns = [
    path("tax/profile", views.ProfileView.as_view()),
    path("tax/years", views.YearsView.as_view()),
    path("tax/years/<int:year>/config", views.YearConfigView.as_view()),
    path("tax/returns/<int:year>", views.ReturnView.as_view()),
    path("tax/returns/<int:year>/inputs", views.ReturnInputsView.as_view()),
    path("tax/returns/<int:year>/compute", views.ReturnComputeView.as_view()),
    path("tax/returns/<int:year>/versions", views.VersionsView.as_view()),
    path("tax/returns/<int:year>/versions/<int:version_id>", views.VersionsView.as_view()),
    path("tax/returns/<int:year>/guide", views.GuideView.as_view()),
    path("tax/returns/<int:year>/guide/progress", views.GuideProgressView.as_view()),
    path("tax/returns/<int:year>/guide.pdf", views.GuidePdfView.as_view()),
    path("tax/returns/<int:year>/exports", views.ExportsView.as_view()),
    path("tax/returns/<int:year>/exports/<int:export_id>/file", views.ExportFileView.as_view()),
    path("tax/returns/<int:year>/<str:action>", views.ReturnActionView.as_view()),
    path("tax/audit", views.TaxAuditView.as_view()),
]
