from django.db import models
from django.conf import settings
from django.db.models import UniqueConstraint

# Create your models here.

class Business(models.Model):

    class BusinessChoices(models.TextChoices):
        SALON_CATEGORY = 'SC', 'Salon Category'
        BARBER_SHOP_CATEGORY = 'BSC', 'Barbershop Category'
        SPA_CATEGORY = 'SP', 'Spa Category'
        BEAUTY_CLINIC_CATEGORY = 'BCC', 'Beauty Clinic Category'
        SALON_PROFESSIONAL_CATEGORY = 'SPC', 'Salon Professional Category'

    business_owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    business_name = models.CharField(max_length=255)
    business_description = models.TextField()
    business_category = models.CharField(max_length=3, choices=BusinessChoices.choices, default=BusinessChoices.SALON_CATEGORY)
    whatsapp_number = models.CharField(max_length=10)
    business_email = models.EmailField()
    business_logo = models.ImageField(upload_to='business_logos/%y/%m/%d/', blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


    class Meta:
        constraints = [
            # This prevents the SAME owner from creating the SAME business name under the SAME category twice.
            UniqueConstraint(
                fields=['business_owner', 'business_name', 'business_category'],
                name='unique_owner_business_category',
            )
        ]
    
    def __str__(self):
        return f"Business {self.business_name} under {self.business_category}"


class Service(models.Model):
  business = models.ForeignKey(
      Business, on_delete=models.CASCADE, related_name='services'
  )
  service_name = models.CharField(max_length=255)
  service_description = models.TextField(blank=True, null=True)
  price = models.DecimalField(max_digits=10, decimal_places=2)
  duration_minutes = models.PositiveIntegerField(
      help_text='Duration of the service in minutes'
  )
  is_available = models.BooleanField(default=True)
  created_at = models.DateTimeField(auto_now_add=True)
  updated_at = models.DateTimeField(auto_now=True)

  @property
  def business_category(self):
    #  dynamically render the business category from the linked business
     return self.business.business_category

  class Meta:
    constraints = [
        # This prevents a specific business from creating duplicate service names
        UniqueConstraint(
            fields=['business', 'service_name'],
            name='unique_service_per_business',
        )
    ]

  def __str__(self):
    return f'{self.service_name} ({self.business.business_name})'


class BusinessStaff(models.Model):

    business = models.ForeignKey(
        Business,
        on_delete=models.CASCADE,
        related_name="business_staff"
    )

    staff_name = models.CharField(max_length=100)
    staff_email = models.EmailField()
    id_number = models.CharField(max_length=8)

    is_available = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def business_category(self):
        return self.business.business_category

    class Meta:
        constraints = [
            UniqueConstraint(
                fields=["business", "staff_email"],
                name="unique_staff_email_per_business"
            ),
            UniqueConstraint(
                fields=["business", "id_number"],
                name="unique_staff_id_per_business"
            ),
        ]

    def __str__(self):
        return f"{self.staff_name} working at {self.business}"




