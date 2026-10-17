"""
"Sign in with Shizuha ID" — OAuth 2.0 authorization-code + PKCE (BFF flow).

Monorepo decoupling (PLAT-352/PLAT-353): mail is a standalone OAuth client of
Shizuha ID. The Django backend acts as the confidential client: it holds the
client_secret (env, from k8s Secret `shizuha-mail-oauth`), generates PKCE +
state, and exchanges the authorization code server-side. The SPA only ever
sees the resulting tokens (delivered in the URL fragment, which never reaches
server logs).

Flow:
  GET  /api/oauth/login/     -> 302 to ID /oauth/authorize (PKCE, state)
  GET  /api/oauth/callback/  -> exchanges code at ID /oauth/token,
                                302 to FRONTEND_URL#access=...&refresh=...

Env:
  SHIZUHA_OAUTH_CLIENT_ID / SHIZUHA_OAUTH_CLIENT_SECRET  (k8s Secret)
  SHIZUHA_OAUTH_ISSUER      e.g. http://shizuha-id:8001  (token endpoint, in-cluster)
  SHIZUHA_OAUTH_AUTHORIZE_URL  public authorize URL shown to the browser,
                            e.g. https://id.shizuha.com/oauth/authorize
  MAIL_PUBLIC_URL           public base of this mail deployment (for redirect_uri)
  MAIL_FRONTEND_URL         where to land the SPA after login (default '/')
"""
import base64
import hashlib
import json
import logging
import os
import secrets
import urllib.parse
import urllib.request

from django.core import signing
from django.http import HttpResponseRedirect, JsonResponse

logger = logging.getLogger(__name__)

_STATE_COOKIE = 'shizuha_mail_oauth_state'
_STATE_MAX_AGE = 600  # seconds


def _env(name, default=''):
    return os.environ.get(name, default)


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode('ascii')


def _redirect_uri() -> str:
    base = _env('MAIL_PUBLIC_URL', 'http://localhost:8014').rstrip('/')
    return f'{base}/api/auth/oauth/callback/'


def oauth_login(request):
    """Kick off authz-code + PKCE: sign state+verifier into a cookie, redirect to ID."""
    client_id = _env('SHIZUHA_OAUTH_CLIENT_ID')
    if not client_id:
        return JsonResponse({'error': 'oauth_not_configured'}, status=503)

    verifier = _b64url(secrets.token_bytes(32))
    challenge = _b64url(hashlib.sha256(verifier.encode('ascii')).digest())
    state = _b64url(secrets.token_bytes(16))

    authorize = _env('SHIZUHA_OAUTH_AUTHORIZE_URL', '/id/oauth/authorize')
    params = {
        'response_type': 'code',
        'client_id': client_id,
        'redirect_uri': _redirect_uri(),
        'scope': _env('SHIZUHA_OAUTH_SCOPE', 'openid profile email mail'),
        'state': state,
        'code_challenge': challenge,
        'code_challenge_method': 'S256',
    }
    resp = HttpResponseRedirect(f'{authorize}?{urllib.parse.urlencode(params)}')
    resp.set_cookie(
        _STATE_COOKIE,
        signing.dumps({'state': state, 'verifier': verifier}, salt='mail-oauth'),
        max_age=_STATE_MAX_AGE, httponly=True, samesite='Lax',
        secure=_env('MAIL_PUBLIC_URL', '').startswith('https'),
    )
    return resp


def oauth_callback(request):
    """Server-side code exchange (confidential client), then hand tokens to the SPA."""
    error = request.GET.get('error')
    if error:
        return JsonResponse({'error': error,
                             'error_description': request.GET.get('error_description', '')},
                            status=400)
    code = request.GET.get('code')
    state = request.GET.get('state', '')
    if not code:
        return JsonResponse({'error': 'invalid_request', 'error_description': 'missing code'},
                            status=400)
    try:
        saved = signing.loads(request.COOKIES.get(_STATE_COOKIE, ''),
                              salt='mail-oauth', max_age=_STATE_MAX_AGE)
    except signing.BadSignature:
        return JsonResponse({'error': 'invalid_state'}, status=400)
    if not secrets.compare_digest(saved.get('state', ''), state):
        return JsonResponse({'error': 'state_mismatch'}, status=400)

    token_url = (_env('SHIZUHA_OAUTH_TOKEN_URL')
                 or _env('SHIZUHA_OAUTH_ISSUER', 'http://shizuha-id:8001').rstrip('/') + '/api/oauth/token')
    body = urllib.parse.urlencode({
        'grant_type': 'authorization_code',
        'code': code,
        'redirect_uri': _redirect_uri(),
        'client_id': _env('SHIZUHA_OAUTH_CLIENT_ID'),
        'client_secret': _env('SHIZUHA_OAUTH_CLIENT_SECRET'),
        'code_verifier': saved['verifier'],
    }).encode('ascii')
    req = urllib.request.Request(
        token_url, data=body,
        headers={'Content-Type': 'application/x-www-form-urlencoded'})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            tokens = json.loads(r.read())
    except Exception:  # noqa: BLE001
        # MAIL-5 §2.5 / MAIL-8 C5+C6: never surface the provider error body or
        # the code-bearing exchange request to the client or logs. str(exc) on a
        # urllib HTTPError/URLError can carry the token-endpoint response body
        # (and may echo the request), and the authorization `code` must never be
        # logged. Emit only a redacted marker; return a generic error to the SPA.
        logger.warning('OAuth token exchange failed (provider error redacted)')
        return JsonResponse({'error': 'token_exchange_failed'}, status=502)

    frontend = _env('MAIL_FRONTEND_URL', '/')
    fragment = urllib.parse.urlencode({
        'access': tokens.get('access_token', ''),
        'refresh': tokens.get('refresh_token', ''),
    })
    resp = HttpResponseRedirect(f'{frontend}#{fragment}')
    resp.delete_cookie(_STATE_COOKIE)
    return resp
