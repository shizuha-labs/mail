from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase, override_settings

from mail.services.mail_service import MailService, MailServiceError, get_mail_service
from mail.services.smtp_client import SMTPClient


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


@override_settings(
    SHIZUHA_OAUTH_TOKEN_URL='http://id.test/api/oauth/token',
    MAIL_DELEGATED_TOKEN_EXCHANGE_ENABLED=True,
)
class ManagedIdentityBearerTests(SimpleTestCase):
    @mock.patch(
        'mail.services.delegated_token.exchange_delegated_mail_token',
        return_value='delegated-short-lived',
    )
    def test_request_bearer_is_exchanged_for_jmap(self, exchange):
        request = SimpleNamespace(META={'HTTP_AUTHORIZATION': 'Bearer short-lived-id-token'})
        service = get_mail_service(request)
        client = service.create_jmap_client(managed_account())
        self.assertEqual(
            client._get_headers()['Authorization'],
            'Bearer delegated-short-lived',
        )
        exchange.assert_called_once_with('short-lived-id-token')

    @mock.patch('mail.services.delegated_token.exchange_delegated_mail_token')
    def test_preflight_bearer_is_consumed_without_second_exchange(self, exchange):
        request = SimpleNamespace(
            META={'HTTP_AUTHORIZATION': 'Bearer short-lived-id-token'},
            _mail74_provider_bearer='already-exchanged',
        )
        first = get_mail_service(request)
        second = get_mail_service(request)
        client = first.create_jmap_client(managed_account())
        self.assertIs(first, second)
        self.assertEqual(first.oauth_access_token, '')
        self.assertEqual(client.auth_token, 'already-exchanged')
        exchange.assert_not_called()

    @mock.patch(
        'mail.services.delegated_token.exchange_delegated_mail_token',
        return_value='delegated-short-lived',
    )
    def test_request_bearer_is_exchanged_for_smtp_xoauth2(self, exchange):
        service = MailService(oauth_access_token='short-lived-id-token')
        client = service.create_smtp_client(managed_account())
        self.assertEqual(client.oauth_access_token, 'delegated-short-lived')
        self.assertEqual(client.password, '')
        exchange.assert_called_once_with('short-lived-id-token')

    def test_managed_account_fails_closed_without_request_bearer(self):
        with self.assertRaisesRegex(
            MailServiceError,
            'authenticated request OAuth token',
        ):
            MailService().create_jmap_client(managed_account())

    @mock.patch('mail.services.smtp_client.smtplib.SMTP')
    def test_smtp_client_uses_xoauth2_instead_of_password_login(self, smtp_class):
        connection = smtp_class.return_value
        connection.docmd.return_value = (235, b'2.7.0 Authentication successful')
        client = SMTPClient(
            host='smtp.example.test',
            username='accounting@shizuha.com',
            oauth_access_token='short-lived-id-token',
            security='starttls',
        )
        self.assertTrue(client.connect())
        command, value = connection.docmd.call_args.args
        self.assertEqual(command, 'AUTH')
        self.assertTrue(value.startswith('XOAUTH2 '))
        connection.login.assert_not_called()
