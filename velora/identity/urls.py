from django.urls import path
from .views import RegistrationView, LoginView, LogoutView

# relevant view

urlpatterns = [
    path('api/auth/register/', RegistrationView.as_view(), name='Registration_First_Users'),
    path('api/auth/login/', LoginView.as_view(), name='Login_First_Users'),
    path('api/auth/logout/', LogoutView.as_view(), name='Logout_first_users')
]
