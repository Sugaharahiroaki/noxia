from django.urls import path

from . import views

urlpatterns = [
    path("health", views.HealthView.as_view()),
    path("auth/csrf", views.CsrfView.as_view()),
    path("auth/login", views.LoginView.as_view()),
    path("auth/logout", views.LogoutView.as_view()),
    path("auth/me", views.MeView.as_view()),
    path("auth/reauth", views.ReauthView.as_view()),
    path("auth/select-store", views.SelectStoreView.as_view()),
    path("auth/change-password", views.ChangePasswordView.as_view()),
    path("auth/accept-invite", views.AcceptInviteView.as_view()),
    path("store/settings", views.StoreSettingsView.as_view()),
    path("store/business-date", views.BusinessDateView.as_view()),
    path("audit/logs", views.AuditLogsView.as_view()),
    path("companies/<int:company_id>", views.CompanyDetailView.as_view()),
    path("companies/<int:company_id>/stores", views.CompanyStoresView.as_view()),
    path("companies/<int:company_id>/stores/<int:store_id>", views.CompanyStoreDetailView.as_view()),
    path("companies/<int:company_id>/members", views.CompanyMembersView.as_view()),
    path("companies/<int:company_id>/invitations", views.CompanyInvitationsView.as_view()),
    path("platform/companies", views.PlatformCompaniesView.as_view()),
    path("platform/companies/<int:company_id>", views.PlatformCompanyDetailView.as_view()),
    path("platform/companies/<int:company_id>/stores", views.PlatformCompanyStoresView.as_view()),
    path("platform/users", views.PlatformUsersView.as_view()),
    path("platform/access-logs", views.PlatformAccessLogsView.as_view()),
]
