"""
OAuth service for connecting external mail accounts (Gmail, Outlook) — MAIL-41.

Implements the authorization-code + PKCE flow for providers configured in
MAIL_OAUTH_PROVIDERS. The backend acts as the confidential client:
  - client_secret never leaves the server
  - PKCE + signed-cookie state prevent CSRF and code-injection
  - Access/refresh tokens are encrypted at rest via CredentialService
  - The authorization code is exchanged immediately and never logged (MAIL-8 C6)
"""
import base64
import hashlib
import json
import logging
import secrets
import urllib.parse
import urllib.request
import urllib.error
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)


class OAuthProviderError(Exception):
    pass


class OAuthProviderNotConfigured(OAuthProviderError):
    pass


class OAuthStateError(OAuthProviderError):
    pass


def _provider_cfg(provider: str) -> dict:
    """Return config dict for provider; raise if unknown or unconfigured."""
    providers = getattr(settings, 'MAIL_OAUTH_PROVIDERS', {})
    cfg = providers.get(provider)
    if cfg is None:
        raise OAuthProviderNotConfigured(f"Unknown OAuth provider: {provider!r}")
    if not cfg.get('client_id'):
        raise OAuthProviderNotConfigured(
            f"OAuth provider {provider!r} has no client_id configured "
            "(set GMAIL_OAUTH_CLIENT_ID / OUTLOOK_OAUTH_CLIENT_ID env vars)"
        )
    return cfg


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode('ascii')


def build_authorization_url(provider: str, state: str, code_challenge: str) -> str:
    """Return the provider authorization URL the browser should be redirected to."""
    cfg = _provider_cfg(provider)
    redirect_uri = getattr(settings, 'MAIL_OAUTH_REDIRECT_URI', '')
    params = {
        'response_type': 'code',
        'client_id': cfg['client_id'],
        'redirect_uri': redirect_uri,
        'scope': ' '.join(cfg['scopes']),
        'state': state,
        'code_challenge': code_challenge,
        'code_challenge_method': 'S256',
        'access_type': 'offline',   # needed by Google for refresh_token
        'prompt': 'consent',        # force refresh_token on every consent (Google)
    }
    return f"{cfg['auth_url']}?{urllib.parse.urlencode(params)}"


def generate_pkce_pair() -> tuple[str, str]:
    """Return (verifier, challenge) for PKCE-S256."""
    verifier = _b64url(secrets.token_bytes(32))
    challenge = _b64url(hashlib.sha256(verifier.encode('ascii')).digest())
    return verifier, challenge


def generate_state() -> str:
    return _b64url(secrets.token_bytes(16))


def exchange_code(provider: str, code: str, verifier: str) -> dict:
    """
    Exchange authorization code for access + refresh tokens.
    Returns the raw token response dict.
    The code is consumed immediately and NEVER logged (MAIL-8 C6).
    """
    cfg = _provider_cfg(provider)
    redirect_uri = getattr(settings, 'MAIL_OAUTH_REDIRECT_URI', '')
    body = urllib.parse.urlencode({
        'grant_type': 'authorization_code',
        'code': code,
        'redirect_uri': redirect_uri,
        'client_id': cfg['client_id'],
        'client_secret': cfg['client_secret'],
        'code_verifier': verifier,
    }).encode('ascii')
    req = urllib.request.Request(
        cfg['token_url'],
        data=body,
        headers={'Content-Type': 'application/x-www-form-urlencoded'},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except Exception:
        # Never surface provider error body or the exchange request (MAIL-8 C5+C6)
        logger.warning(
            'OAuth token exchange failed for provider %r (provider error redacted)',
            provider,
        )
        raise OAuthProviderError('token_exchange_failed')


def fetch_user_email(provider: str, access_token: str) -> str:
    """Fetch the authenticated user's email address from the provider userinfo endpoint."""
    cfg = _provider_cfg(provider)
    req = urllib.request.Request(
        cfg['userinfo_url'],
        headers={'Authorization': f'Bearer {access_token}'},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            info = json.loads(resp.read())
    except Exception:
        logger.warning('Failed to fetch userinfo from provider %r (error redacted)', provider)
        raise OAuthProviderError('userinfo_fetch_failed')

    email = info.get('email') or info.get('mail') or info.get('userPrincipalName', '')
    if not email:
        raise OAuthProviderError('provider did not return an email address')
    return email


def token_expiry_from_response(token_response: dict):
    """Parse expires_in from token response and return an aware UTC datetime."""
    expires_in = token_response.get('expires_in')
    if expires_in:
        return timezone.now() + timedelta(seconds=int(expires_in))
    return None
