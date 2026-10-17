from django.urls import path

from . import views

urlpatterns = [
    path("store/", views.PublicStoreInfoView.as_view(), name="public-store-info"),
]
