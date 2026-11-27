from rest_framework import serializers
from rest_framework.validators import UniqueTogetherValidator

from .mixins import owned_businesses
from .models import Business, Service, BusinessStaff


class OwnedBusinessField(serializers.PrimaryKeyRelatedField):
    """A ``business`` field that only accepts businesses the requester owns."""

    def get_queryset(self):
        request = self.context.get("request")
        return owned_businesses(getattr(request, "user", None))


class BusinessSerializers(serializers.ModelSerializer):

    business_category_label = serializers.CharField(
        source="get_business_category_display", read_only=True
    )

    class Meta:
        model = Business
        fields = [
            "id",
            "business_name",
            "business_description",
            "business_category",
            "business_category_label",
            "whatsapp_number",
            "business_email",
            "business_logo",
            "number_of_service_users",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate(self, attrs):
        # The model has a unique constraint on (owner, name, category); the owner is
        # set by the view, so DRF cannot check it for us. Check it here so users get
        # a friendly 400 instead of a database error.
        request = self.context.get("request")
        name = attrs.get("business_name", getattr(self.instance, "business_name", None))
        category = attrs.get("business_category", getattr(self.instance, "business_category", None))
        if request and name and category:
            clash = Business.objects.filter(
                business_owner=request.user,
                business_name__iexact=name,
                business_category=category,
            )
            if self.instance:
                clash = clash.exclude(pk=self.instance.pk)
            if clash.exists():
                raise serializers.ValidationError(
                    {"business_name": ["You already have a business with this name in this category."]}
                )
        return attrs


class ServiceSerializer(serializers.ModelSerializer):

    business = OwnedBusinessField()
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
            "buffer_before_minutes",
            "buffer_after_minutes",
            "deposit_amount",
            "resource",
            "is_available",
            "business_category",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at", "business_category"]
        validators = [
            UniqueTogetherValidator(
                queryset=Service.objects.all(),
                fields=["business", "service_name"],
                message="A service with this name already exists for this business.",
            )
        ]

    def validate_duration_minutes(self, value):
        if value < 5:
            raise serializers.ValidationError("A service must last at least 5 minutes.")
        return value

    def validate_price(self, value):
        if value < 0:
            raise serializers.ValidationError("Price cannot be negative.")
        return value

    def validate(self, attrs):
        business = attrs.get("business", getattr(self.instance, "business", None))
        resource = attrs.get("resource")
        if resource is not None and business is not None and resource.business_id != business.id:
            raise serializers.ValidationError({"resource": ["This resource belongs to another business."]})
        return attrs


class BusinessStaffSerializer(serializers.ModelSerializer):

    business = OwnedBusinessField()
    business_category = serializers.CharField(source="business.business_category", read_only=True)
    skills = serializers.PrimaryKeyRelatedField(
        many=True, required=False, queryset=Service.objects.all()
    )

    class Meta:
        model = BusinessStaff
        fields = [
            "id",
            "business",
            "staff_name",
            "staff_email",
            "staff_phone",
            "id_number",
            "skills",
            "is_available",
            "business_category",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "business_category", "created_at", "updated_at"]

    def validate(self, attrs):
        business = attrs.get("business", getattr(self.instance, "business", None))
        for service in attrs.get("skills", []):
            if business is not None and service.business_id != business.id:
                raise serializers.ValidationError({"skills": ["Every skill must be a service of this business."]})

        # Friendly duplicate checks for the two unique constraints.
        for field, label in (("staff_email", "email"), ("id_number", "ID number")):
            value = attrs.get(field)
            if business is not None and value:
                clash = BusinessStaff.objects.filter(business=business, **{field: value})
                if self.instance:
                    clash = clash.exclude(pk=self.instance.pk)
                if clash.exists():
                    raise serializers.ValidationError({field: [f"A staff member with this {label} already exists."]})
        return attrs
