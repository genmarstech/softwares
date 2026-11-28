from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers

from .models import CustomerAccount
from .phones import normalize_phone

PHONE_HINT = 'Enter a valid phone number, e.g. 0712 345 678 or +254 712 345 678.'


class PhoneField(serializers.CharField):
    def to_internal_value(self, data):
        value = normalize_phone(super().to_internal_value(data))
        if not value:
            raise serializers.ValidationError(PHONE_HINT)
        return value


class RegisterSerializer(serializers.Serializer):
    business = serializers.CharField(max_length=80)
    full_name = serializers.CharField(max_length=255)
    phone_number = PhoneField(max_length=30)
    email = serializers.EmailField(required=False, allow_blank=True)
    password = serializers.CharField(write_only=True, max_length=128)

    def validate_full_name(self, value):
        value = ' '.join(value.split())
        if len(value) < 2:
            raise serializers.ValidationError('Please enter your name.')
        return value

    def validate_phone_number(self, value):
        if CustomerAccount.objects.filter(phone_number=value).exists():
            raise serializers.ValidationError(
                'This phone number already has a Velora account. Sign in instead.')
        return value

    def validate_email(self, value):
        return (value or '').strip().lower()

    def validate_password(self, value):
        validate_password(value)
        return value


class LoginSerializer(serializers.Serializer):
    business = serializers.CharField(max_length=80, required=False, allow_blank=True)
    phone_number = PhoneField(max_length=30)
    password = serializers.CharField(write_only=True, max_length=128)


class ProfileSerializer(serializers.Serializer):
    full_name = serializers.CharField(max_length=255, required=False)
    email = serializers.EmailField(required=False, allow_blank=True)

    def validate_full_name(self, value):
        value = ' '.join(value.split())
        if len(value) < 2:
            raise serializers.ValidationError('Please enter your name.')
        return value
