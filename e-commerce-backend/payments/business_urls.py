from django.urls import path

from . import views

# Customer-initiated — this one does know its business, from the URL, same
# as checkout/cart. See payments/urls.py for the callback, which stays
# global.
urlpatterns = [
    path("payments/mpesa/initiate/", views.InitiatePaymentView.as_view(), name="mpesa-initiate"),
]
