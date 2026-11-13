from rest_framework import serializers
from .models import Business, Service, BusinessStaff
from rest_framework.validators import UniqueTogetherValidator

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

class ServiceSerializer(serializers.ModelSerializer):

    business_category = serializers.CharField(read_only=True)

    class Meta:
        model = Service

        fields = [
            "id",
            "business",
            "service_name",
            "service_description",
            "price",
            "duration_minutes",
            "is_available",
            "business_category",
            "created_at",
            "updated_at",
        ]

        read_only_fields = [
            "id",
            "created_at",
            "updated_at",
            "business_category",
        ]

        validators = [
            UniqueTogetherValidator(
                queryset=Service.objects.all(),
                fields=["business", "service_name"],
                message="A service with this name already exists for this business.",
            )
        ]

    def validate_business(self, value):
        request = self.context.get("request")

        if request and value.business_owner_id != request.user.id:
            raise serializers.ValidationError(
                "You cannot add services to this business."
            )

        return value


from rest_framework import serializers
from .models import BusinessStaff, Business


class BusinessStaffSerializer(serializers.ModelSerializer):

    business = serializers.PrimaryKeyRelatedField(
        queryset=Business.objects.none()
    )

    business_category = serializers.CharField(
        source="business.business_category",
        read_only=True
    )

    class Meta:
        model = BusinessStaff

        fields = [
            "id",
            "business",
            "staff_name",
            "staff_email",
            "id_number",
            "is_available",
            "business_category",
            "created_at",
            "updated_at",
        ]

        read_only_fields = [
            "id",
            "business_category",
            "created_at",
            "updated_at",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        request = self.context.get("request")

        if request and request.user.is_authenticated:
            self.fields["business"].queryset = Business.objects.filter(
                business_owner=request.user
            )