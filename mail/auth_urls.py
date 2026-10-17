"""
Authentication URLs for Shizuha Mail.

Authentication is handled by shizuha-id. This provides a minimal
auth endpoint to verify tokens and get current user info.
"""
from django.urls import path
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from .oauth_views import oauth_login, oauth_callback


class UserView(APIView):
    """Get current authenticated user info."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        return Response({
            'id': user.id,
            'email': user.email,
            'username': user.username,
            'first_name': getattr(user, 'first_name', ''),
            'last_name': getattr(user, 'last_name', ''),
            'date_joined': getattr(user, 'date_joined', None),
        })


urlpatterns = [
    path('user/', UserView.as_view(), name='auth-user'),
    # "Sign in with Shizuha ID" (OAuth authz-code + PKCE, BFF) — PLAT-353
    path('oauth/login/', oauth_login, name='oauth-login'),
    path('oauth/callback/', oauth_callback, name='oauth-callback'),
]
