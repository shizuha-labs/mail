"""
OAuth views for connecting external mail accounts (Gmail, Outlook) — MAIL-41.

Two endpoints:
  GET  /api/accounts/oauth/initiate/?provider=gmail
       Returns {redirect_url, state} for the SPA to redirect the user.
       Sets a signed HttpOnly state cookie (PKCE verifier + provider + user_id).

  GET  /api/accounts/oauth/callback/?code=...&state=...
       Validates state cookie, exchanges the code, fetches user email,
       creates (or updates) a MailAccount with auth_type='oauth2'.
       Returns the serialized MailAccount JSON.

Both endpoints require an authenticated Shizuha ID session (IsAuthenticated).
The authorization code is consumed immediately and never logged (MAIL-8 C6).
"""
import logging
import secrets

from django.conf import settings
from django.core import signing

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework import status

from .models import MailAccount
from .serializers import MailAccountSerializer
from .services.credential_service import get_credential_service
from .services.oauth_mail_service import (
    OAuthProviderError,
    OAuthProviderNotConfigured,
    build_authorization_url,
    exchange_code,
    fetch_user_email,
    generate_pkce_pair,
    generate_state,
    token_expiry_from_response,
)

logger = logging.getLogger(__name__)

_OAUTH_STATE_COOKIE = 'shizuha_mail_account_oauth_state'
_STATE_MAX_AGE = 600  # 10 min; short enough to limit replay risk


class OAuthAccountInitiateView(APIView):
    """
    GET /api/accounts/oauth/initiate/?provider=gmail

    Returns the provider authorization URL for the SPA to redirect the user,
    and sets a signed HttpOnly cookie with PKCE state bound to the user's ID.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        provider = request.query_params.get('provider', '').strip().lower()
        if not provider:
            return Response({'error': 'provider query param required'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            verifier, challenge = generate_pkce_pair()
            state = generate_state()
            redirect_url = build_authorization_url(provider, state, challenge)
        except OAuthProviderNotConfigured as exc:
            return Response({'error': str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)

        cookie_payload = signing.dumps(
            {'state': state, 'verifier': verifier, 'provider': provider, 'user_id': request.user.id},
            salt='mail-account-oauth',
        )

        resp = Response({'redirect_url': redirect_url, 'state': state})
        is_secure = getattr(settings, 'MAIL_OAUTH_REDIRECT_URI', '').startswith('https')
        resp.set_cookie(
            _OAUTH_STATE_COOKIE,
            cookie_payload,
            max_age=_STATE_MAX_AGE,
            httponly=True,
            samesite='Lax',
            secure=is_secure,
        )
        return resp


class OAuthAccountCallbackView(APIView):
    """
    GET /api/accounts/oauth/callback/?code=...&state=...

    Validates the signed state cookie, exchanges the authorization code for
    tokens, fetches the user's email address, then creates or updates a
    MailAccount with auth_type='oauth2' and encrypted OAuth tokens.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        error = request.query_params.get('error')
        if error:
            return Response(
                {'error': error, 'error_description': request.query_params.get('error_description', '')},
                status=status.HTTP_400_BAD_REQUEST,
            )

        code = request.query_params.get('code')
        incoming_state = request.query_params.get('state', '')
        if not code:
            return Response({'error': 'invalid_request', 'error_description': 'missing code'},
                            status=status.HTTP_400_BAD_REQUEST)

        # Validate signed state cookie
        try:
            saved = signing.loads(
                request.COOKIES.get(_OAUTH_STATE_COOKIE, ''),
                salt='mail-account-oauth',
                max_age=_STATE_MAX_AGE,
            )
        except signing.BadSignature:
            return Response({'error': 'invalid_state'}, status=status.HTTP_400_BAD_REQUEST)

        if not secrets.compare_digest(saved.get('state', ''), incoming_state):
            return Response({'error': 'state_mismatch'}, status=status.HTTP_400_BAD_REQUEST)

        # Bind to authenticated user (prevents session fixation across users)
        if saved.get('user_id') != request.user.id:
            return Response({'error': 'user_mismatch'}, status=status.HTTP_400_BAD_REQUEST)

        provider = saved.get('provider', '')
        verifier = saved.get('verifier', '')

        # Exchange code → tokens (code never logged: MAIL-8 C6)
        try:
            token_response = exchange_code(provider, code, verifier)
        except OAuthProviderError:
            return Response({'error': 'token_exchange_failed'}, status=status.HTTP_502_BAD_GATEWAY)

        access_token = token_response.get('access_token', '')
        refresh_token = token_response.get('refresh_token', '')
        if not access_token:
            return Response({'error': 'no_access_token'}, status=status.HTTP_502_BAD_GATEWAY)

        # Fetch email address from provider userinfo
        try:
            email_address = fetch_user_email(provider, access_token)
        except OAuthProviderError:
            return Response({'error': 'userinfo_fetch_failed'}, status=status.HTTP_502_BAD_GATEWAY)

        # Encrypt tokens at rest
        cred_svc = get_credential_service()
        enc_access = cred_svc.encrypt_password(access_token)
        enc_refresh = cred_svc.encrypt_password(refresh_token) if refresh_token else None
        expiry = token_expiry_from_response(token_response)

        # Build IMAP/SMTP config from provider defaults
        from django.conf import settings as django_settings
        provider_cfg = getattr(django_settings, 'MAIL_OAUTH_PROVIDERS', {}).get(provider, {})

        is_first = not MailAccount.objects.filter(user_id=request.user.id, is_active=True).exists()

        account, created = MailAccount.objects.update_or_create(
            user_id=request.user.id,
            email_address=email_address,
            defaults=dict(
                auth_type='oauth2',
                oauth_provider=provider,
                encrypted_oauth_access_token=enc_access,
                encrypted_oauth_refresh_token=enc_refresh,
                oauth_token_expiry=expiry,
                incoming_server_type='imap',
                incoming_host=provider_cfg.get('imap_host', ''),
                incoming_port=provider_cfg.get('imap_port', 993),
                incoming_security=provider_cfg.get('imap_security', 'ssl'),
                incoming_username=email_address,
                outgoing_host=provider_cfg.get('smtp_host', ''),
                outgoing_port=provider_cfg.get('smtp_port', 587),
                outgoing_security=provider_cfg.get('smtp_security', 'starttls'),
                outgoing_username=email_address,
                outgoing_requires_auth=True,
                is_active=True,
                is_default=is_first,
            ),
        )

        resp = Response(
            MailAccountSerializer(account).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )
        resp.delete_cookie(_OAUTH_STATE_COOKIE)
        return resp
