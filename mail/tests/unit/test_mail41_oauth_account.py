"""
Unit tests for MAIL-41: OAuth account creation flow.

Tests cover:
- OAuthAccountInitiateView: missing/unknown/unconfigured provider
- OAuthAccountInitiateView: well-formed response + signed cookie
- OAuthAccountCallbackView: missing code, bad state, user mismatch
- OAuthAccountCallbackView: successful account creation (new + update)
- oauth_mail_service helpers: PKCE generation, URL building, token expiry
"""
import json
from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.core import signing
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from mail.models import MailAccount
from mail.services.oauth_mail_service import (
    OAuthProviderNotConfigured,
    build_authorization_url,
    generate_pkce_pair,
    generate_state,
    token_expiry_from_response,
)

MOCK_PROVIDERS = {
    'gmail': {
        'client_id': 'test-client-id',
        'client_secret': 'test-client-secret',
        'auth_url': 'https://accounts.google.com/o/oauth2/v2/auth',
        'token_url': 'https://oauth2.googleapis.com/token',
        'userinfo_url': 'https://openidconnect.googleapis.com/v1/userinfo',
        'scopes': ['https://mail.google.com/', 'openid', 'email'],
        'imap_host': 'imap.gmail.com',
        'imap_port': 993,
        'imap_security': 'ssl',
        'smtp_host': 'smtp.gmail.com',
        'smtp_port': 587,
        'smtp_security': 'starttls',
    },
}

MOCK_REDIRECT_URI = 'http://testserver/api/accounts/oauth/callback/'


class FakeUser:
    id = 42
    is_authenticated = True


# ---------------------------------------------------------------------------
# Service-layer unit tests
# ---------------------------------------------------------------------------

class PKCEGenerationTest(TestCase):
    def test_verifier_and_challenge_differ(self):
        v, c = generate_pkce_pair()
        self.assertNotEqual(v, c)

    def test_state_is_random(self):
        states = {generate_state() for _ in range(10)}
        self.assertEqual(len(states), 10)

    def test_token_expiry_none_when_missing(self):
        self.assertIsNone(token_expiry_from_response({}))

    def test_token_expiry_parsed(self):
        before = timezone.now()
        expiry = token_expiry_from_response({'expires_in': 3600})
        self.assertAlmostEqual(
            (expiry - before).total_seconds(), 3600, delta=2
        )


@override_settings(MAIL_OAUTH_PROVIDERS=MOCK_PROVIDERS, MAIL_OAUTH_REDIRECT_URI=MOCK_REDIRECT_URI)
class BuildAuthorizationUrlTest(TestCase):
    def test_returns_google_url(self):
        url = build_authorization_url('gmail', 'mystate', 'mychallenge')
        self.assertIn('accounts.google.com', url)
        self.assertIn('mystate', url)
        self.assertIn('mychallenge', url)
        self.assertIn('S256', url)

    def test_unknown_provider_raises(self):
        with self.assertRaises(OAuthProviderNotConfigured):
            build_authorization_url('yahoo', 'state', 'challenge')


@override_settings(MAIL_OAUTH_PROVIDERS={'gmail': {'client_id': '', 'scopes': []}})
class UnconfiguredProviderTest(TestCase):
    def test_raises_when_client_id_empty(self):
        with self.assertRaises(OAuthProviderNotConfigured):
            build_authorization_url('gmail', 'state', 'challenge')


# ---------------------------------------------------------------------------
# View integration tests (no live DB needed for most cases)
# ---------------------------------------------------------------------------

@override_settings(MAIL_OAUTH_PROVIDERS=MOCK_PROVIDERS, MAIL_OAUTH_REDIRECT_URI=MOCK_REDIRECT_URI)
class OAuthInitiateViewTest(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(user=FakeUser())

    def test_missing_provider_returns_400(self):
        resp = self.client.get('/api/accounts/oauth/initiate/')
        self.assertEqual(resp.status_code, 400)

    def test_unknown_provider_returns_503(self):
        resp = self.client.get('/api/accounts/oauth/initiate/?provider=yahoo')
        self.assertEqual(resp.status_code, 503)

    def test_gmail_returns_redirect_url_and_state(self):
        resp = self.client.get('/api/accounts/oauth/initiate/?provider=gmail')
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn('redirect_url', data)
        self.assertIn('state', data)
        self.assertIn('accounts.google.com', data['redirect_url'])
        self.assertIn(data['state'], data['redirect_url'])

    def test_cookie_is_set(self):
        resp = self.client.get('/api/accounts/oauth/initiate/?provider=gmail')
        self.assertIn('shizuha_mail_account_oauth_state', resp.cookies)


@override_settings(MAIL_OAUTH_PROVIDERS=MOCK_PROVIDERS, MAIL_OAUTH_REDIRECT_URI=MOCK_REDIRECT_URI)
class OAuthCallbackViewTest(TestCase):
    """
    Tests for the callback view; the external HTTP calls to provider are mocked.
    Uses a real in-memory SQLite DB for MailAccount creation checks.
    """

    def _make_state_cookie(self, state, verifier, provider='gmail', user_id=42):
        return signing.dumps(
            {'state': state, 'verifier': verifier, 'provider': provider, 'user_id': user_id},
            salt='mail-account-oauth',
        )

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(user=FakeUser())

    def test_missing_code_returns_400(self):
        self.client.cookies['shizuha_mail_account_oauth_state'] = self._make_state_cookie('s', 'v')
        resp = self.client.get('/api/accounts/oauth/callback/?state=s')
        self.assertEqual(resp.status_code, 400)

    def test_bad_cookie_returns_400(self):
        self.client.cookies['shizuha_mail_account_oauth_state'] = 'invalid-garbage'
        resp = self.client.get('/api/accounts/oauth/callback/?code=abc&state=s')
        self.assertEqual(resp.status_code, 400)

    def test_state_mismatch_returns_400(self):
        self.client.cookies['shizuha_mail_account_oauth_state'] = self._make_state_cookie('correct-state', 'v')
        resp = self.client.get('/api/accounts/oauth/callback/?code=abc&state=wrong-state')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('state_mismatch', resp.json()['error'])

    def test_user_mismatch_returns_400(self):
        cookie = self._make_state_cookie('s', 'v', user_id=999)
        self.client.cookies['shizuha_mail_account_oauth_state'] = cookie
        resp = self.client.get('/api/accounts/oauth/callback/?code=abc&state=s')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('user_mismatch', resp.json()['error'])

    def test_provider_error_returns_400(self):
        cookie = self._make_state_cookie('s', 'v')
        self.client.cookies['shizuha_mail_account_oauth_state'] = cookie
        resp = self.client.get('/api/accounts/oauth/callback/?error=access_denied&state=s')
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()['error'], 'access_denied')

    @patch('mail.oauth_account_views.exchange_code')
    @patch('mail.oauth_account_views.fetch_user_email')
    @patch('mail.oauth_account_views.get_credential_service')
    def test_creates_account_with_auth_type_oauth2(self, mock_cred_svc, mock_fetch_email, mock_exchange):
        mock_exchange.return_value = {'access_token': 'acc', 'refresh_token': 'ref', 'expires_in': 3600}
        mock_fetch_email.return_value = 'alice@gmail.com'
        cred = MagicMock()
        cred.encrypt_password.return_value = b'encrypted'
        mock_cred_svc.return_value = cred

        state = 'teststate123'
        cookie = self._make_state_cookie(state, 'verifier123')
        self.client.cookies['shizuha_mail_account_oauth_state'] = cookie

        resp = self.client.get(f'/api/accounts/oauth/callback/?code=authcode&state={state}')
        self.assertEqual(resp.status_code, 201)
        data = resp.json()
        self.assertEqual(data['email_address'], 'alice@gmail.com')
        self.assertEqual(data['auth_type'], 'oauth2')

        account = MailAccount.objects.get(user_id=42, email_address='alice@gmail.com')
        self.assertEqual(account.auth_type, 'oauth2')
        self.assertEqual(account.oauth_provider, 'gmail')
        self.assertIsNotNone(account.encrypted_oauth_access_token)
        self.assertIsNotNone(account.encrypted_oauth_refresh_token)
        self.assertIsNotNone(account.oauth_token_expiry)
        self.assertEqual(account.incoming_host, 'imap.gmail.com')
        self.assertEqual(account.outgoing_host, 'smtp.gmail.com')

    @patch('mail.oauth_account_views.exchange_code')
    @patch('mail.oauth_account_views.fetch_user_email')
    @patch('mail.oauth_account_views.get_credential_service')
    def test_update_existing_account_returns_200(self, mock_cred_svc, mock_fetch_email, mock_exchange):
        MailAccount.objects.create(
            user_id=42,
            email_address='alice@gmail.com',
            auth_type='password',
            incoming_host='imap.gmail.com',
            incoming_port=993,
            incoming_security='ssl',
            incoming_username='alice@gmail.com',
            outgoing_host='smtp.gmail.com',
            outgoing_port=587,
            outgoing_security='starttls',
            outgoing_username='alice@gmail.com',
        )

        mock_exchange.return_value = {'access_token': 'acc2', 'refresh_token': 'ref2', 'expires_in': 3600}
        mock_fetch_email.return_value = 'alice@gmail.com'
        cred = MagicMock()
        cred.encrypt_password.return_value = b'enc2'
        mock_cred_svc.return_value = cred

        state = 'st2'
        cookie = self._make_state_cookie(state, 'v2')
        self.client.cookies['shizuha_mail_account_oauth_state'] = cookie

        resp = self.client.get(f'/api/accounts/oauth/callback/?code=code2&state={state}')
        self.assertEqual(resp.status_code, 200)
        account = MailAccount.objects.get(user_id=42, email_address='alice@gmail.com')
        self.assertEqual(account.auth_type, 'oauth2')
        self.assertEqual(account.oauth_provider, 'gmail')
