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

    def create(self, validated_data):
        return FirstUser.objects.create_user(**validated_data)
