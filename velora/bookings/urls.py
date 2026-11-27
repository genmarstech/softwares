from django.urls import path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register(r'bookings', views.BookingViewSet, basename='bookings')
router.register(r'customers', views.CustomerViewSet, basename='customers')
router.register(r'working-hours', views.WorkingHoursViewSet, basename='working-hours')
router.register(r'time-off', views.TimeOffViewSet, basename='time-off')
router.register(r'locations', views.LocationViewSet, basename='locations')
router.register(r'resources', views.ResourceViewSet, basename='resources')
router.register(r'waitlist', views.WaitlistViewSet, basename='waitlist')
router.register(r'notifications', views.NotificationViewSet, basename='notifications')

urlpatterns = [
    path('policy/', views.BookingPolicyView.as_view(), name='booking-policy'),
    path('availability/', views.OwnerAvailabilityView.as_view(), name='owner-availability'),
    path('analytics/', views.AnalyticsView.as_view(), name='analytics'),
    path('analytics/export/', views.AnalyticsExportView.as_view(), name='analytics-export'),

    # Public, customer-facing booking API. <business_id> is the business's
    # booking-link slug (glow-salon) or, for older links, its numeric id.
    path('public/manage/<uuid:token>/', views.PublicManageView.as_view(), name='public-manage'),
    path('public/manage/<uuid:token>/cancel/', views.PublicCancelView.as_view(), name='public-cancel'),
    path('public/manage/<uuid:token>/reschedule/', views.PublicRescheduleView.as_view(),
         name='public-reschedule'),
    path('public/manage/<uuid:token>/confirm/', views.PublicConfirmView.as_view(), name='public-confirm'),
    path('public/manage/<uuid:token>/preferences/', views.PublicPreferencesView.as_view(),
         name='public-preferences'),
    path('public/<str:business_id>/', views.PublicBusinessView.as_view(), name='public-business'),
    path('public/<str:business_id>/availability/', views.PublicAvailabilityView.as_view(),
         name='public-availability'),
    path('public/<str:business_id>/available-days/', views.PublicAvailableDaysView.as_view(),
         name='public-available-days'),
    path('public/<str:business_id>/bookings/', views.PublicBookingCreateView.as_view(), name='public-book'),
    path('public/<str:business_id>/track/', views.PublicTrackView.as_view(), name='public-track'),
    path('public/<str:business_id>/waitlist/', views.PublicWaitlistView.as_view(), name='public-waitlist'),
] + router.urls
