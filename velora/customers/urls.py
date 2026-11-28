from django.urls import path

from . import views

urlpatterns = [
    path('session/', views.SessionView.as_view(), name='customer-session'),
    path('register/', views.RegisterView.as_view(), name='customer-register'),
    path('login/', views.LoginView.as_view(), name='customer-login'),
    path('logout/', views.LogoutView.as_view(), name='customer-logout'),
    path('join/', views.JoinView.as_view(), name='customer-join'),
    path('bookings/', views.MyBookingsView.as_view(), name='customer-bookings'),
]
