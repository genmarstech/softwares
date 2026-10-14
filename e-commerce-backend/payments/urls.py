from django.urls import path

from . import views

# Safaricom calls this directly — it has no idea what a business slug is,
# and never will, so it stays unprefixed and global. The order it refers to
# still carries its own `business`; see MpesaCallbackView for how that's
# used. The trailing <token> is the callback-authenticity check — see
# MPESA_CALLBACK_SECRET in config/settings.py and the view's docstring.
urlpatterns = [
    path("payments/mpesa/callback/<str:token>/", views.MpesaCallbackView.as_view(), name="mpesa-callback"),
]
