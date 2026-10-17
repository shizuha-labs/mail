from unittest import mock

import httpx
from django.test import SimpleTestCase

from mail.services.mail_service import MailService, MailServiceError


class JmapTransportBoundaryTests(SimpleTestCase):
    def test_connect_timeout_becomes_mail_service_error(self):
        account = mock.Mock(
            incoming_server_type='jmap',
            incoming_security='ssl',
            incoming_host='retired.example.com',
            incoming_port=443,
            incoming_username='legacy@shizuha.com',
            encrypted_incoming_password=None,
            settings={},
            auth_type='password',
            encrypted_oauth_access_token=None,
        )
        service = MailService()

        with mock.patch(
            'mail.services.mail_service.httpx.Client',
            side_effect=httpx.ConnectTimeout('provider timed out'),
        ):
            with self.assertRaisesRegex(MailServiceError, 'JMAP provider unavailable'):
                service.sync_folders(account)
