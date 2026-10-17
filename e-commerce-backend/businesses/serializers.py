from rest_framework import serializers

from .models import Business, BusinessMembership


class BusinessSerializer(serializers.ModelSerializer):
    class Meta:
        model = Business
        fields = [
            "id", "name", "slug", "status", "country", "timezone",
            "default_currency", "contact_email", "contact_phone", "created_at",
        ]
        read_only_fields = ["id", "status", "created_at"]


class BusinessMembershipSerializer(serializers.ModelSerializer):
    user_email = serializers.EmailField(source="user.email", read_only=True)

    class Meta:
        model = BusinessMembership
        fields = ["id", "user", "user_email", "role", "status", "invited_by", "created_at"]
        read_only_fields = ["id", "invited_by", "created_at"]

    def validate(self, attrs):
        # UniqueConstraint (unlike the older unique_together Meta option)
        # isn't auto-introspected into a DRF validator, so a duplicate
        # membership would otherwise surface as a raw IntegrityError (500)
        # rather than a clean 400 — same reasoning as catalog/serializers.py.
        business = self.context.get("business")
        user = attrs.get("user")
        if business and user and self.instance is None:
            if BusinessMembership.objects.filter(business=business, user=user).exists():
                raise serializers.ValidationError({"user": "This user is already a member of this business."})
        return attrs


class MyBusinessMembershipSerializer(serializers.ModelSerializer):
    """
    For GET /api/me/businesses/ — the one thing BusinessMembershipSerializer
    above can't answer, because it's built for `/api/ops/<slug>/members/`
    where the business is already known from the URL: "which businesses
    does the CALLER belong to." The business itself is nested here because
    that's the entire point of this endpoint — a caller with zero
    memberships gets `[]`, not an error, and has no other way to discover
    even one business's slug.
    """

    business = BusinessSerializer(read_only=True)

    class Meta:
        model = BusinessMembership
        fields = ["id", "business", "role", "status", "created_at"]
        read_only_fields = fields
