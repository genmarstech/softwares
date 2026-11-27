from datetime import time

from django.db import migrations


def create_defaults(apps, schema_editor):
    Business = apps.get_model('business', 'Business')
    BookingPolicy = apps.get_model('bookings', 'BookingPolicy')
    WorkingHours = apps.get_model('bookings', 'WorkingHours')
    for business in Business.objects.all():
        BookingPolicy.objects.get_or_create(
            business=business,
            defaults={'notification_channels': ['email', 'sms'], 'reminder_hours': [24, 2]},
        )
        if not WorkingHours.objects.filter(business=business, staff__isnull=True).exists():
            WorkingHours.objects.bulk_create([
                WorkingHours(business=business, weekday=day, start_time=time(9), end_time=time(18),
                             is_closed=(day == 6))
                for day in range(7)
            ])


class Migration(migrations.Migration):

    dependencies = [
        ('bookings', '0001_initial'),
        ('business', '0006_business_number_of_service_users_and_more'),
    ]

    operations = [
        migrations.RunPython(create_defaults, migrations.RunPython.noop),
    ]
