from django.db import migrations, models
from django.db.models import Q


def backfill_slugs(apps, schema_editor):
    from business.slugs import unique_business_slug

    Business = apps.get_model('business', 'Business')
    for business in Business.objects.filter(Q(slug__isnull=True) | Q(slug='')).order_by('id'):
        business.slug = unique_business_slug(business.business_name, exclude_pk=business.pk, model=Business)
        business.save(update_fields=['slug'])


class Migration(migrations.Migration):

    dependencies = [
        ('business', '0006_business_number_of_service_users_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='business',
            name='slug',
            field=models.SlugField(max_length=80, null=True, db_index=False),
        ),
        migrations.RunPython(backfill_slugs, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='business',
            name='slug',
            field=models.SlugField(max_length=80, unique=True),
        ),
    ]
