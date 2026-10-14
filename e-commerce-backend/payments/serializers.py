from rest_framework import serializers

from .models import Payment


class PaymentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Payment
        fields = [
            "id", "order", "provider", "amount", "currency", "phone_number",
            "status", "receipt_number", "created_at",
        ]
        read_only_fields = fields


class InitiatePaymentSerializer(serializers.Serializer):
    order_reference = serializers.CharField()
    phone = serializers.CharField()
