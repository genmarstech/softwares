from django.shortcuts import render
from django.contrib.auth import authenticate, login, logout
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.authentication import SessionAuthentication, BasicAuthentication
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from .serializers import RegistrationSerializer

class RegistrationView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = RegistrationSerializer(data=request.data)
        if serializer.is_valid():
            user = serializer.save()
            login(request, user)
            request.session.modified = True  # Force immediate cookie emission
            return Response(
                {'message': 'User Registered Successfully'},
                status=status.HTTP_201_CREATED
            )
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

class LoginView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        email = request.data.get('email')
        password = request.data.get('password')

        user = authenticate(request, username=email, password=password)

        if user is not None:
            login(request, user)  
            request.session.modified = True  # Tell Django to write cookie changes immediately
            request.session.save()           # Manually commit session record to DB
            
            # Flattened user structure to match frontend response expectations
            return Response({
                "message": "Login successful.", 
                "authenticated": True, 
                "session_id": request.session.session_key, 
                "user": {
                    "id": user.id, 
                    "email": user.email, 
                    "name": getattr(user, 'full_name', '')
                }
            }, status=status.HTTP_200_OK)
        
        return Response({"error": "Invalid email or password.", "authenticated": False}, status=status.HTTP_400_BAD_REQUEST)


class getCurrentUser(APIView):
    # Include both Session and Basic fallback classes to bypass global DRF overrides
    authentication_classes = [SessionAuthentication, BasicAuthentication]
    permission_classes = [AllowAny]

    def get(self, request):
        user = request.user

        if not user.is_authenticated:
            return Response(
                {
                    "authenticated": False,
                    "user": None
                },
                status=status.HTTP_200_OK
            )

        return Response(
            {
                "authenticated": True,
                "user": {
                    "id": user.id,
                    "email": user.email,
                    "name": getattr(user, 'full_name', '')
                }
            },
            status=status.HTTP_200_OK
        )

class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        logout(request)  
        return Response({"message": "Logged out successfully."}, status=status.HTTP_200_OK)
