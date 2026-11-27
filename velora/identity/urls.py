from django.urls import path
from .views import CsrfTokenView, RegistrationView, LoginView, LogoutView, getCurrentUser

urlpatterns = [
    path('api/auth/csrf/', CsrfTokenView.as_view(), name='csrf_token'),
    path('api/auth/register/', RegistrationView.as_view(), name='Registration_First_Users'),
    path('api/auth/login/', LoginView.as_view(), name='Login_First_Users'),
    path('api/auth/logout/', LogoutView.as_view(), name='Logout_first_users'),
    path('api/auth/me/', getCurrentUser.as_view(), name='current_user'),
]
