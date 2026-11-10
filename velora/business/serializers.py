from rest_framework import serializers
from .models import Business

# relevant serializers

class BusinessSerializers(serializers.ModelSerializer):

    class Meta:
        model = Business
        fields = [
            "id",
            "business_name",
            "business_description",
            "business_category",
            "whatsapp_number",
            "business_email",
            "business_logo",
            "created_at",
            "updated_at",
        ]

        read_only_fields = [
            "id",
            "created_at",
            "updated_at",
        ]

