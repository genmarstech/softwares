from rest_framework import serializers

from .models import StoreSettings


class StoreSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = StoreSettings
        fields = ["currency", "default_delivery_fee", "low_stock_default_threshold", "theme"]


class PublicStoreInfoSerializer(serializers.Serializer):
    """
    The anonymous-readable counterpart to StoreSettingsSerializer — a
    storefront visitor (and next/image's build-time image-optimizer calls,
    see commerce-platform's own PublicStoreInfo usage) needs the business's
    real display name and currency to render correctly, neither of which
    any public endpoint exposed before this (see commerce-platform's
    src/lib/format.ts, which derived a name from the slug and omitted a
    currency symbol entirely for exactly this reason). Deliberately a
    narrow, separate serializer rather than widening
    BusinessSerializer/StoreSettingsSerializer's own field lists — contact
    info and every other Business/StoreSettings field stay owner/staff-only.
    """

    name = serializers.CharField(source="business.name", read_only=True)
    currency = serializers.CharField(read_only=True)
    theme = serializers.CharField(read_only=True)
