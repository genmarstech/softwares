from rest_framework import serializers
from django.contrib.auth import get_user_model

FirstUser = get_user_model()



class RegistrationSerializer(serializers.ModelSerializer):

    password = serializers.CharField(
        write_only=True,
        style={'input_type': 'password'}
    )

    class Meta:
        model = FirstUser
        fields = [
            'id',
            'full_name',
            'email',
            'phone_number',
            'password',
        ]

    def validate_email(self, value):
        value = value.strip().lower()
        if FirstUser.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("An account with this email already exists.")
        return value

    def validate_password(self, value):
        from django.contrib.auth.password_validation import validate_password
        validate_password(value)
        return value

    def create(self, validated_data):
        return FirstUser.objects.create_user(**validated_data)
