from django.urls import path

from . import views

urlpatterns = [
    path("dashboard/", views.DashboardView.as_view(), name="ops-dashboard"),
    path("settings/", views.StoreSettingsView.as_view(), name="ops-settings"),
]
