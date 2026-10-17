"""MAIL-74: request-scoped delegated token for managed Stalwart accounts."""
from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase, override_settings

from mail.services.delegated_token import (
    DelegatedTokenError,
    DelegatedTokenExpired,
    exchange_delegated_mail_token,
)
from mail.services.mail_service import MailService, MailServiceError


def managed_account():
    return SimpleNamespace(
        settings={'managed_identity': True},
        auth_type='oauth2',
        encrypted_oauth_access_token=None,
        encrypted_incoming_password=None,
        encrypted_outgoing_password=None,
        incoming_server_type='jmap',
        incoming_host='stalwart-shadow.mail-shadow.svc.cluster.local',
        incoming_port=8080,
        incoming_security='none',
        incoming_username='accounting@shizuha.com',
        outgoing_host='stalwart-shadow-mail.mail-shadow.svc.cluster.local',
        outgoing_port=587,
        outgoing_security='starttls',
        outgoing_username='accounting@shizuha.com',
        email_address='accounting@shizuha.com',
        from_name='Accounting',
        display_name='Shizuha Mail',
    )


class ExchangeUnitTests(SimpleTestCase):
    @override_settings(SHIZUHA_OAUTH_TOKEN_URL='http://id.test/api/oauth/token')
    @mock.patch('mail.services.delegated_token.httpx.Client')
    def test_exchange_returns_access_token(self, client_cls):
        response = mock.Mock()
        response.status_code = 200
        response.json.return_value = {
            'access_token': 'delegated-mail-token',
            'issued_token_type': 'urn:ietf:params:oauth:token-type:access_token',
            'token_type': 'Bearer',
            'expires_in': 60,
            'scope': 'openid email',
        }
        client_cls.return_value.__enter__.return_value.post.return_value = response
        token = exchange_delegated_mail_token('subject-platform-token')
        self.assertEqual(token, 'delegated-mail-token')

    @override_settings(SHIZUHA_OAUTH_TOKEN_URL='http://id.test/api/oauth/token')
    @mock.patch('mail.services.delegated_token.httpx.Client')
    def test_exchange_fails_closed_on_http_error(self, client_cls):
        response = mock.Mock()
        response.status_code = 403
        client_cls.return_value.__enter__.return_value.post.return_value = response
        with self.assertRaises(DelegatedTokenError):
            exchange_delegated_mail_token('subject-platform-token')

    @override_settings(SHIZUHA_OAUTH_TOKEN_URL='http://id.test/api/oauth/token')
    @mock.patch('mail.services.delegated_token.httpx.Client')
    def test_exchange_401_raises_delegated_token_expired(self, client_cls):
        """MAIL-131: ID rejecting the SUBJECT token (invalid_grant -> 401) is a
        caller-expiry, not a mailbox fault — raise the typed subclass so callers
        can re-authenticate."""
        response = mock.Mock()
        response.status_code = 401
        client_cls.return_value.__enter__.return_value.post.return_value = response
        with self.assertRaises(DelegatedTokenExpired) as ctx:
            exchange_delegated_mail_token('subject-platform-token')
        self.assertIn('re-authenticate', str(ctx.exception))

    @override_settings(SHIZUHA_OAUTH_TOKEN_URL='http://id.test/api/oauth/token')
    @mock.patch('mail.services.delegated_token.httpx.Client')
    def test_exchange_rejects_refresh_token_in_response(self, client_cls):
        response = mock.Mock()
        response.status_code = 200
        response.json.return_value = {
            'access_token': 'x',
            'refresh_token': 'nope',
            'issued_token_type': 'urn:ietf:params:oauth:token-type:access_token',
            'token_type': 'Bearer',
            'expires_in': 60,
            'scope': 'openid email',
        }
        client_cls.return_value.__enter__.return_value.post.return_value = response
        with self.assertRaises(DelegatedTokenError):
            exchange_delegated_mail_token('subject-platform-token')

    @override_settings(SHIZUHA_OAUTH_TOKEN_URL='http://id.test/api/oauth/token')
    @mock.patch('mail.services.delegated_token.httpx.Client')
    def test_exchange_rejects_response_outside_delegation_contract(self, client_cls):
        response = mock.Mock()
        response.status_code = 200
        client_cls.return_value.__enter__.return_value.post.return_value = response
        base = {
            'access_token': 'x',
            'issued_token_type': 'urn:ietf:params:oauth:token-type:access_token',
            'token_type': 'Bearer',
            'expires_in': 60,
            'scope': 'openid email',
        }
        invalid = (
            {'issued_token_type': 'urn:ietf:params:oauth:token-type:refresh_token'},
            {'token_type': 'MAC'},
            {'expires_in': 61},
            {'expires_in': 'invalid'},
            {'scope': 'openid email profile'},
        )
        for override in invalid:
            with self.subTest(override=override):
                response.json.return_value = {**base, **override}
                with self.assertRaises(DelegatedTokenError):
                    exchange_delegated_mail_token('subject-platform-token')


class MailServiceDelegatedTests(SimpleTestCase):
    @override_settings(
        SHIZUHA_OAUTH_TOKEN_URL='http://id.test/api/oauth/token',
        MAIL_DELEGATED_TOKEN_EXCHANGE_ENABLED=True,
    )
    @mock.patch('mail.services.delegated_token.exchange_delegated_mail_token')
    def test_managed_account_uses_exchanged_token_not_subject(self, exchange):
        exchange.return_value = 'delegated-for-stalwart'
        service = MailService(oauth_access_token='broker-relay-subject')
        client = service.create_jmap_client(managed_account())
        self.assertEqual(client.auth_token, 'delegated-for-stalwart')
        exchange.assert_called_once_with('broker-relay-subject')
        service.create_jmap_client(managed_account())
        self.assertEqual(exchange.call_count, 1)

    @override_settings(MAIL_DELEGATED_TOKEN_EXCHANGE_ENABLED=True)
    @mock.patch(
        'mail.services.delegated_token.exchange_delegated_mail_token',
        side_effect=DelegatedTokenError('no entitlement'),
    )
    def test_exchange_failure_blocks_provider_client(self, exchange):
        service = MailService(oauth_access_token='broker-relay-subject')
        with self.assertRaisesRegex(MailServiceError, 'no entitlement'):
            service.create_jmap_client(managed_account())
        exchange.assert_called_once()

    def test_managed_without_request_bearer_still_fails_closed(self):
        with self.assertRaisesRegex(MailServiceError, 'authenticated request OAuth token'):
            MailService().create_jmap_client(managed_account())
