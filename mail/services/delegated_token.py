"""MAIL-74: request-scoped delegated access token for managed Stalwart auth.

Exchanges the caller's Shizuha ID access token for a short-lived
``shizuha-mail``-audience token (openid email, <=60s). Never logs or stores
the resulting bearer. Fail closed on any exchange error.
"""
from __future__ import annotations

import logging

import httpx
from django.conf import settings

from mail.redaction import safe_exception_text

logger = logging.getLogger(__name__)


class DelegatedTokenError(Exception):
    """Exchange failed; do not call the mail provider."""

    def __init__(self, message: str = 'delegated token exchange failed'):
        super().__init__(safe_exception_text(message))


class DelegatedTokenExpired(DelegatedTokenError):
    """MAIL-131: the ID token endpoint rejected the CALLER's subject token.

    The mailbox is healthy; the request's own Shizuha ID access token is
    expired or revoked. The caller must re-authenticate (fresh access token)
    and retry — there is nothing wrong with the mail account or Stalwart.
    """


def exchange_delegated_mail_token(subject_access_token: str) -> str:
    """POST token-exchange grant; return access_token string only."""
    if not subject_access_token or not str(subject_access_token).strip():
        raise DelegatedTokenError('missing subject access token')

    token_url = getattr(settings, 'SHIZUHA_OAUTH_TOKEN_URL', '')
    if not token_url:
        raise DelegatedTokenError('SHIZUHA_OAUTH_TOKEN_URL not configured')

    payload = {
        'grant_type': 'urn:ietf:params:oauth:grant-type:token-exchange',
        'subject_token': subject_access_token.strip(),
        'subject_token_type': 'urn:ietf:params:oauth:token-type:access_token',
        'requested_token_type': 'urn:ietf:params:oauth:token-type:access_token',
    }
    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.post(
                token_url,
                data=payload,
                headers={'Accept': 'application/json'},
            )
    except Exception as exc:
        logger.warning('delegated token exchange transport failure (details redacted)')
        raise DelegatedTokenError('token exchange transport failure') from exc

    if response.status_code == 401:
        # MAIL-131: ID rejects the SUBJECT token ("invalid_grant: subject token
        # invalid or expired"). The caller must re-auth; the mailbox is fine.
        logger.warning('delegated token exchange rejected caller token (401)')
        raise DelegatedTokenExpired(
            'caller access token expired or revoked — re-authenticate '
            '(token exchange HTTP 401)'
        )
    if response.status_code != 200:
        logger.warning('delegated token exchange HTTP %s', response.status_code)
        raise DelegatedTokenError(
            f'token exchange rejected with HTTP {response.status_code}'
        )

    try:
        data = response.json()
    except Exception as exc:
        raise DelegatedTokenError('token exchange returned non-JSON') from exc

    access = (data.get('access_token') or '').strip()
    if not access:
        raise DelegatedTokenError('token exchange response missing access_token')
    if data.get('refresh_token'):
        logger.error('delegated token exchange unexpectedly returned refresh_token')
        raise DelegatedTokenError('token exchange must not return refresh_token')
    if data.get('issued_token_type') != 'urn:ietf:params:oauth:token-type:access_token':
        raise DelegatedTokenError('token exchange returned unexpected token type')
    if str(data.get('token_type') or '').lower() != 'bearer':
        raise DelegatedTokenError('token exchange returned non-bearer token')
    try:
        expires_in = int(data.get('expires_in'))
    except (TypeError, ValueError) as exc:
        raise DelegatedTokenError('token exchange returned invalid expiry') from exc
    if not 0 < expires_in <= 60:
        raise DelegatedTokenError('token exchange token lifetime exceeds contract')
    if (data.get('scope') or '').strip() != 'openid email':
        raise DelegatedTokenError('token exchange returned unexpected scope')
    return access


def resolve_provider_bearer(*, subject_access_token: str, managed_identity: bool) -> str:
    """Return the bearer to send to JMAP/SMTP for this request only."""
    if not managed_identity:
        return subject_access_token or ''
    if not getattr(settings, 'MAIL_DELEGATED_TOKEN_EXCHANGE_ENABLED', True):
        raise DelegatedTokenError(
            'managed mail requires delegated token exchange (disabled)'
        )
    return exchange_delegated_mail_token(subject_access_token)
