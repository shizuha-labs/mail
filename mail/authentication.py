"""
Custom JWT Authentication for Shizuha Mail

This authentication class validates JWT tokens issued by Shizuha ID
without requiring the user to exist in the local database.

In a microservices architecture with unified login (like Atlassian):
- Shizuha ID issues JWT tokens
- Other services (Mail, Pulse, Admin) validate and trust those tokens
- User data is fetched from the token claims, not the local database
"""

import logging
from rest_framework import authentication, exceptions
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.tokens import AccessToken
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from django.contrib.auth import get_user_model

logger = logging.getLogger(__name__)
User = get_user_model()


class FederatedUser:
    """
    A lightweight user object for federated authentication.
    Used when the user exists in Shizuha ID but not in the local database.
    """
    def __init__(self, user_id, email=None, username=None, first_name=None, last_name=None,
                 date_joined=None, enabled_services=None):
        from django.utils import timezone
        self.id = user_id
        self.pk = user_id
        self.email = email or f"user_{user_id}@shizuha.id"
        self.username = username or f"user_{user_id}"
        self.first_name = first_name or ""
        self.last_name = last_name or ""
        self.date_joined = date_joined or timezone.now()
        self.is_authenticated = True
        self.is_active = True
        self.is_anonymous = False
        self.is_staff = False
        self.is_superuser = False
        # None = unrestricted (staff/superuser), [] = no services, [...] = specific services
        self.enabled_services = enabled_services

    def __str__(self):
        return f"FederatedUser({self.id})"

    def save(self, *args, **kwargs):
        pass  # No-op for federated users


# ───────────────────────── OAuth 2.0 advisory + enforcement ─────────────────
# Inlined from packages/shizuha-auth: Phase 2/4 of OAuth migration. Services
# that import the shared package should prefer that copy; this duplicate
# keeps parity while the migration to the shared package is in-flight.

def _oauth_advisory_claim_check(validated_token):
    """Log mismatched `aud`/`iss` claims without rejecting. Returns status dict."""
    import os as _os
    from django.conf import settings as _st
    service_name = getattr(_st, 'SHIZUHA_SERVICE_NAME', '') or _os.environ.get('SHIZUHA_SERVICE_NAME', '')
    expected_issuer = getattr(_st, 'SHIZUHA_EXPECTED_ISSUER', '') or _os.environ.get('SHIZUHA_EXPECTED_ISSUER', '')
    aud = validated_token.get('aud')
    if aud is not None and aud not in ('*', ['*']):
        aud_list = aud if isinstance(aud, list) else [aud]
        if service_name and service_name not in aud_list:
            logger.warning(
                f"JWT aud={aud_list!r} does not include service={service_name!r}. "
                f"Phase 2 advisory — token accepted."
            )
    iss = validated_token.get('iss')
    if iss is not None and expected_issuer and iss != expected_issuer:
        logger.warning(
            f"JWT iss={iss!r} does not match expected={expected_issuer!r}. "
            f"Phase 2 advisory — token accepted."
        )


def _oauth_enforce_audience_claim(validated_token):
    """Phase 4: when SHIZUHA_AUD_ENFORCE=True, reject tokens with mismatched aud."""
    import os as _os
    from django.conf import settings as _st
    from rest_framework_simplejwt.exceptions import InvalidToken as _InvalidToken
    enforce = bool(getattr(_st, 'SHIZUHA_AUD_ENFORCE', False)) or \
        _os.environ.get('SHIZUHA_AUD_ENFORCE', '').lower() in ('1', 'true', 'yes')
    if not enforce:
        return
    service_name = getattr(_st, 'SHIZUHA_SERVICE_NAME', '') or _os.environ.get('SHIZUHA_SERVICE_NAME', '')
    if not service_name:
        return
    aud = validated_token.get('aud')
    if aud is None or aud == '*' or aud == ['*']:
        return
    aud_list = aud if isinstance(aud, list) else [aud]
    if service_name not in aud_list:
        logger.warning(
            f"JWT rejected: aud={aud_list!r} does not include required service={service_name!r}"
        )
        raise _InvalidToken(f"Token audience {aud_list} does not include '{service_name}'")


# ───────────────────────── OAuth 2.0 RS256 validation ──────────────────────
# Phase 5 of OAuth migration: accept tokens signed with RS256 (shizuha-id's
# RSA key, public half published at /id/.well-known/jwks.json). Falls back
# from default HS256 validation so legacy tokens keep working during grace.

class _Rs256Token(dict):
    """Lightweight dict-like token — satisfies simplejwt get_user() interface."""
    pass


_JWKS_CACHE = {'keys': {}, 'fetched_at': 0}
_JWKS_TTL = 600  # seconds


def _jwks_fetch_keys():
    import json as _json, os as _os, time as _time, urllib.request as _urlreq
    import jwt as _jwt
    if _time.time() - _JWKS_CACHE['fetched_at'] < _JWKS_TTL and _JWKS_CACHE['keys']:
        return _JWKS_CACHE['keys']
    issuer = (_os.environ.get('SHIZUHA_EXPECTED_ISSUER') or
              'http://shizuha-id:8001').rstrip('/')
    jwks_url = _os.environ.get('SHIZUHA_OAUTH_JWKS_URL') or f'{issuer}/.well-known/jwks.json'
    try:
        resp = _urlreq.urlopen(jwks_url, timeout=5)
        jwks = _json.loads(resp.read())
        _JWKS_CACHE['keys'] = {
            k['kid']: _jwt.algorithms.RSAAlgorithm.from_jwk(_json.dumps(k))
            for k in jwks.get('keys', []) if k.get('kid')
        }
        _JWKS_CACHE['fetched_at'] = _time.time()
    except Exception as _exc:
        logger.warning(f'JWKS fetch from {jwks_url} failed: {_exc}')
    return _JWKS_CACHE['keys']


def _try_rs256_validate(raw_token):
    """Return a _Rs256Token on success, None if not RS256, raise if bad RS256."""
    import jwt as _jwt
    from rest_framework_simplejwt.exceptions import InvalidToken as _InvalidToken
    try:
        header = _jwt.get_unverified_header(raw_token)
    except Exception:
        return None
    if header.get('alg') != 'RS256':
        return None
    kid = header.get('kid')
    keys = _jwks_fetch_keys()
    key = keys.get(kid) if kid else (next(iter(keys.values())) if keys else None)
    if key is None:
        # Re-fetch once in case of key rotation
        _JWKS_CACHE['fetched_at'] = 0
        keys = _jwks_fetch_keys()
        key = keys.get(kid) if kid else (next(iter(keys.values())) if keys else None)
    if key is None:
        raise _InvalidToken(f'No JWKS key available for kid={kid!r}')
    try:
        payload = _jwt.decode(
            raw_token, key=key, algorithms=['RS256'],
            options={'verify_aud': False, 'verify_exp': True},
        )
    except _jwt.PyJWTError as exc:
        raise _InvalidToken(f'RS256 validation failed: {exc}')
    return _Rs256Token(payload)

class ShizuhaJWTAuthentication(JWTAuthentication):
    """
    Custom JWT authentication that works with Shizuha ID tokens.

    If the user doesn't exist locally, creates a FederatedUser object
    from the token claims instead of failing with 401.
    """


    def get_validated_token(self, raw_token):
        """RS256/JWKS (OAuth, post-decoupling) is the PRIMARY validation path;
        HS256 (legacy shared-secret) is accepted only during the migration
        grace window and dropped once 'Sign in with Shizuha ID' is the sole
        login path (PLAT-352/PLAT-353). `_try_rs256_validate` returns None for
        non-RS256 tokens (alg-based routing) and raises on a bad RS256 token."""
        tok = _try_rs256_validate(raw_token)
        if tok is None:
            import os as _os
            if _os.environ.get('MAIL_DISABLE_HS256', '').lower() in ('1', 'true', 'yes'):
                raise InvalidToken('HS256 tokens rejected (RS256-only mode); sign in with Shizuha ID')
            tok = super().get_validated_token(raw_token)
        from .token_revocation import is_token_revoked
        claims = tok.payload if hasattr(tok, 'payload') else tok
        if is_token_revoked(claims, raw_token):
            raise InvalidToken('Token has been revoked')
        return tok

    def get_user(self, validated_token):
        """
        Get the user from the validated token.
        If user doesn't exist locally, return a FederatedUser.
        """
        _oauth_advisory_claim_check(validated_token)
        _oauth_enforce_audience_claim(validated_token)
        try:
            user_id = validated_token.get('user_id')
            if user_id is None:
                raise InvalidToken('Token contained no recognizable user identification')

            # Extract enabled_services from token
            enabled_services = validated_token.get('enabled_services')
            # Staff/superuser override
            if validated_token.get('is_staff') or validated_token.get('is_superuser'):
                enabled_services = None  # None = unrestricted

            # Extract additional claims up-front.
            email = validated_token.get('email')
            username = validated_token.get('username')
            first_name = validated_token.get('first_name')
            last_name = validated_token.get('last_name')

            # CRITICAL: shizuha-id user_ids collide with local Django PKs
            # (local id=1 is admin@example.com). Blind `User.objects.get(pk=user_id)`
            # would hijack the federated identity with whatever local row sits
            # at that PK. When the token carries an `email` claim (SSO happy
            # path), trust it — don't touch the local DB.
            if email:
                return FederatedUser(
                    user_id=user_id,
                    email=email,
                    username=username,
                    first_name=first_name,
                    last_name=last_name,
                    enabled_services=enabled_services,
                )

            # Legacy / local-login token without email claim — fall back to
            # local DB lookup so Django admin sessions still work.
            try:
                user = User.objects.get(pk=user_id)
                return user
            except User.DoesNotExist:
                logger.debug(
                    f"User {user_id} not found locally and token has no "
                    f"email claim; returning placeholder FederatedUser"
                )
                return FederatedUser(
                    user_id=user_id,
                    email=email,
                    username=username,
                    first_name=first_name,
                    last_name=last_name,
                    enabled_services=enabled_services,
                )

        except KeyError:
            raise InvalidToken('Token contained no recognizable user identification')


class ShizuhaIDAuthentication(authentication.BaseAuthentication):
    """
    Alternative authentication that validates tokens against Shizuha ID service.

    This can be used to verify tokens by calling the Shizuha ID API directly,
    which is useful for:
    - Getting fresh user data
    - Validating token hasn't been revoked
    - Supporting token refresh flows
    """

    def authenticate(self, request):
        auth_header = request.META.get('HTTP_AUTHORIZATION', '')

        if not auth_header.startswith('Bearer '):
            return None

        token = auth_header.split(' ')[1]

        try:
            # Validate the token locally first (fast path)
            validated_token = AccessToken(token)
            user_id = validated_token.get('user_id')

            if user_id is None:
                raise exceptions.AuthenticationFailed('Invalid token')

            # Extract enabled_services from token
            enabled_services = validated_token.get('enabled_services')
            # Staff/superuser override
            if validated_token.get('is_staff') or validated_token.get('is_superuser'):
                enabled_services = None  # None = unrestricted

            # Try local user first
            try:
                user = User.objects.get(pk=user_id)
                return (user, validated_token)
            except User.DoesNotExist:
                # Create federated user
                email = validated_token.get('email')
                username = validated_token.get('username')
                first_name = validated_token.get('first_name')
                last_name = validated_token.get('last_name')

                federated_user = FederatedUser(
                    user_id=user_id,
                    email=email,
                    username=username,
                    first_name=first_name,
                    last_name=last_name,
                    enabled_services=enabled_services
                )
                return (federated_user, validated_token)

        except TokenError as e:
            raise exceptions.AuthenticationFailed(f'Invalid token: {str(e)}')

    def authenticate_header(self, request):
        return 'Bearer realm="api"'
