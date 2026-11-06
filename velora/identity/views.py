from django.shortcuts import render
from django.contrib.auth import authenticate, login, logout
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from .serializers import RegistrationSerializer

# Create your views here.

class RegistrationView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = RegistrationSerializer(data=request.data)

        if serializer.is_valid():
            user = serializer.save()

            login(request, user)

            return Response(
                {'message': 'User Registered Successfully'},
                status=status.HTTP_201_CREATED
            )

        # print("REGISTRATION DATA:", request.data)
        # print("SERIALIZER ERRORS:", serializer.errors)

        return Response(
            serializer.errors,
            status=status.HTTP_400_BAD_REQUEST
        )

class LoginView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        email = request.data.get('email')
        password = request.data.get('password')

        # authenticate() automatically respects USERNAME_FIELD = 'email'
        user = authenticate(request, username=email, password=password)

        if user is not None:
            login(request, user)  # This creates the session and sets the session cookie
            return Response({"message": "Login successful."}, status=status.HTTP_200_OK)
        
        return Response({"error": "Invalid email or password."}, status=status.HTTP_400_BAD_REQUEST)


class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        logout(request)  # Clears the session cookie
        return Response({"message": "Logged out successfully."}, status=status.HTTP_200_OK)


