from django.db import models
from django.conf import settings

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
    
    def __str__(self):
        return f"Business {self.business_name} under {self.business_category}"

