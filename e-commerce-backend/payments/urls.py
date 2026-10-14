from django.urls import path

from . import views

urlpatterns = [
    path("payments/mpesa/initiate/", views.InitiatePaymentView.as_view(), name="mpesa-initiate"),
    path("payments/mpesa/callback/", views.MpesaCallbackView.as_view(), name="mpesa-callback"),
]
