"""Managed-account sync must remain request-scoped and bearer-only."""
from unittest.mock import AsyncMock, Mock, patch

from django.test import override_settings
from mail.tests.active_identity import ActiveIdentityTestCase as TestCase
from rest_framework.test import APIClient

from mail.models import MailAccount, MailFolder, MailMessage
from mail.services.delegated_token import DelegatedTokenError
from mail.tasks import (
    _sync_jmap_messages,
    periodic_sync_all_accounts,
    process_incoming_webhook,
    sync_mailbox_counts,
    sync_new_messages,
)
from mail.tests.helpers import create_test_jwt


class ManagedBackgroundSyncSafetyTests(TestCase):
    def setUp(self):
        self.account = MailAccount.objects.create(
            user_id=74001,
            email_address='managed-sync@shizuha.com',
            incoming_server_type='jmap',
            incoming_host='mail-shadow.shizuha.com',
            incoming_port=443,
            incoming_security='ssl',
            outgoing_host='mail-shadow.shizuha.com',
            auth_type='oauth2',
            settings={'managed_identity': True},
            connection_status='needs_attention',
            connection_error='existing truthful state',
        )

    @patch('mail.tasks._sync_jmap_folder_counts')
    def test_queued_folder_sync_skips_managed_account_without_status_poison(self, sync_impl):
        result = sync_mailbox_counts(
            str(self.account.id),
            expected_user_id=self.account.user_id,
        )

        self.assertEqual(result, 0)
        sync_impl.assert_not_called()
        self.account.refresh_from_db()
        self.assertEqual(self.account.connection_status, 'needs_attention')
        self.assertEqual(self.account.connection_error, 'existing truthful state')

    @patch('mail.tasks._sync_jmap_messages')
    def test_queued_message_sync_skips_managed_account_without_status_poison(self, sync_impl):
        result = sync_new_messages(
            str(self.account.id),
            expected_user_id=self.account.user_id,
        )

        self.assertEqual(result, 0)
        sync_impl.assert_not_called()
        self.account.refresh_from_db()
        self.assertEqual(self.account.connection_status, 'needs_attention')
        self.assertEqual(self.account.connection_error, 'existing truthful state')

    @patch('mail.tasks.sync_new_messages.delay')
    @patch('mail.tasks.sync_mailbox_counts.delay')
    def test_webhook_does_not_queue_managed_account(self, counts_delay, messages_delay):
        process_incoming_webhook({
            'type': 'new_message',
            'account': self.account.email_address,
        })

        counts_delay.assert_not_called()
        messages_delay.assert_not_called()

    @patch('mail.tasks.sync_new_messages.delay')
    @patch('mail.tasks.sync_mailbox_counts.delay')
    def test_periodic_sync_does_not_queue_managed_account(self, counts_delay, messages_delay):
        result = periodic_sync_all_accounts()

        self.assertEqual(result, {'dispatched': 0})
        counts_delay.assert_not_called()
        messages_delay.assert_not_called()


class ManagedRequestSyncTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.account = MailAccount.objects.create(
            user_id=74002,
            email_address='request-sync@shizuha.com',
            incoming_server_type='jmap',
            incoming_host='mail-shadow.shizuha.com',
            incoming_port=443,
            incoming_security='ssl',
            outgoing_host='mail-shadow.shizuha.com',
            auth_type='oauth2',
            settings={'managed_identity': True},
            connection_status='needs_attention',
            connection_error='legacy background failure',
        )
        self.subject_token = create_test_jwt(
            user_id=self.account.user_id,
            email=self.account.email_address,
        )
        self.client.credentials(
            HTTP_AUTHORIZATION=f'Bearer {self.subject_token}',
        )

    @override_settings(
        STALWART_JMAP_URL='http://legacy-central.example:8580',
        UNIFIED_MAIL_JMAP_URL='http://stalwart-shadow.mail-shadow.svc.cluster.local:8080',
    )
    @patch('mail.tasks.sync_new_messages.delay')
    @patch('mail.tasks.sync_mailbox_counts.delay')
    @patch('mail.tasks._sync_jmap_messages', return_value=3)
    @patch('mail.services.stalwart_client.StalwartJMAPClient')
    @patch(
        'mail.services.delegated_token.resolve_provider_bearer',
        return_value='request-scoped-provider-token',
    )
    def test_authenticated_sync_uses_delegated_bearer_inline(
        self,
        exchange,
        client_cls,
        sync_messages,
        counts_delay,
        messages_delay,
    ):
        client = client_cls.return_value
        client.get_session = AsyncMock(return_value={
            'accounts': {'provider-account-id': {}},
        })
        client.get_mailboxes = AsyncMock(return_value=[])

        response = self.client.post(
            '/api/accounts/sync/',
            {'account_id': str(self.account.id)},
            format='json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['status'], 'synced')
        self.assertEqual(response.data['new_messages'], 3)
        exchange.assert_called_once_with(
            subject_access_token=self.subject_token,
            managed_identity=True,
        )
        client_cls.assert_called_once_with(
            user_email=self.account.email_address,
            auth_token='request-scoped-provider-token',
            base_url='http://stalwart-shadow.mail-shadow.svc.cluster.local:8080',
        )
        client.get_session.assert_awaited_once_with()
        client.get_mailboxes.assert_awaited_once_with()
        sync_messages.assert_called_once_with(self.account, client=client)
        counts_delay.assert_not_called()
        messages_delay.assert_not_called()

        self.account.refresh_from_db()
        self.assertEqual(self.account.connection_status, 'connected')
        self.assertEqual(self.account.connection_error, '')
        self.assertIsNone(self.account.encrypted_oauth_access_token)
        self.assertIsNone(self.account.encrypted_incoming_password)
        self.assertNotIn(
            'request-scoped-provider-token',
            str(self.account.settings),
        )

    @patch('mail.tasks.sync_new_messages.delay')
    @patch('mail.tasks.sync_mailbox_counts.delay')
    @patch('mail.services.stalwart_client.StalwartJMAPClient')
    @patch(
        'mail.services.delegated_token.resolve_provider_bearer',
        side_effect=Exception('exchange unavailable'),
    )
    def test_exchange_failure_neither_queues_nor_mutates_account(
        self,
        exchange,
        client_cls,
        counts_delay,
        messages_delay,
    ):
        from mail.services.delegated_token import DelegatedTokenError

        exchange.side_effect = DelegatedTokenError('exchange unavailable')
        response = self.client.post('/api/accounts/sync/', {}, format='json')

        self.assertEqual(response.status_code, 503)
        client_cls.assert_not_called()
        counts_delay.assert_not_called()
        messages_delay.assert_not_called()
        self.account.refresh_from_db()
        self.assertEqual(self.account.connection_status, 'needs_attention')
        self.assertEqual(
            self.account.connection_error,
            'legacy background failure',
        )

    @patch('mail.services.stalwart_client.StalwartJMAPClient')
    @patch(
        'mail.services.delegated_token.resolve_provider_bearer',
        side_effect=DelegatedTokenError('exchange unavailable'),
    )
    def test_caller_token_expired_returns_clear_401_reauths_signal(
        self,
        exchange,
        client_cls,
    ):
        """MAIL-131 regression: ID rejecting the CALLER's subject token (401 /
        invalid_grant) must surface as a clear 401 `caller_token_expired` (re-auth)
        instead of a cryptic generic 503 — the mailbox itself is healthy."""
        from mail.services.delegated_token import DelegatedTokenExpired

        exchange.side_effect = DelegatedTokenExpired(
            'caller access token expired or revoked — re-authenticate '
            '(token exchange HTTP 401)'
        )
        response = self.client.post('/api/accounts/sync/', {}, format='json')

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.data.get('code'), 'caller_token_expired')
        self.assertIn('re-authenticate', response.data.get('error', ''))
        client_cls.assert_not_called()
        # No permanent account-state poisoning for a transient caller-token lapse.
        self.account.refresh_from_db()
        self.assertEqual(self.account.connection_status, 'needs_attention')


class ManagedJmapIngestionTests(TestCase):
    def test_request_client_ingests_stalwart_direct_email_list_shape(self):
        account = MailAccount.objects.create(
            user_id=74003,
            email_address='provider-shape@shizuha.com',
            incoming_server_type='jmap',
            incoming_host='mail-shadow.shizuha.com',
            outgoing_host='mail-shadow.shizuha.com',
            settings={'managed_identity': True},
        )
        folder = MailFolder.objects.create(
            account=account,
            name='Inbox',
            role='inbox',
            jmap_mailbox_id='inbox-provider-id',
        )
        client = type('RequestClient', (), {})()
        client.get_emails = AsyncMock(return_value={
            'emails': [{
                'id': 'provider-message-id',
                'blobId': 'provider-blob-id',
                'threadId': 'provider-thread-id',
                'messageId': ['provider-message@example.com'],
                'subject': 'Delegated sync works',
                'from': [{'email': 'sender@example.com', 'name': 'Sender'}],
                'to': [{'email': account.email_address}],
                'cc': None,
                'bcc': None,
                'keywords': None,
                'size': 42,
                'hasAttachment': False,
                'attachments': None,
            }],
            'total': 1,
        })

        new_count = _sync_jmap_messages(account, client=client)

        self.assertEqual(new_count, 1)
        message = MailMessage.objects.get(
            account=account,
            folder=folder,
            jmap_email_id='provider-message-id',
        )
        self.assertEqual(message.from_address, 'sender@example.com')
        self.assertEqual(message.from_name, 'Sender')
        self.assertEqual(message.cc_addresses, [])
        self.assertEqual(message.bcc_addresses, [])
        self.assertEqual(message.attachment_count, 0)


class StalwartDelegatedHeaderTests(TestCase):
    def test_request_token_precedes_password_and_admin_credentials(self):
        from mail.services.stalwart_client import StalwartJMAPClient

        client = StalwartJMAPClient(
            user_email='managed@shizuha.com',
            user_password='legacy-password',
            auth_token='request-token',
        )

        self.assertEqual(
            client._get_headers()['Authorization'],
            'Bearer request-token',
        )

    @patch('mail.services.stalwart_client.httpx.AsyncClient')
    def test_session_discovery_follows_stalwart_canonical_redirect(
        self,
        async_client_cls,
    ):
        from mail.services.stalwart_client import StalwartJMAPClient

        response = Mock()
        response.status_code = 200
        response.json.return_value = {'accounts': {'provider-account': {}}}
        request_client = async_client_cls.return_value.__aenter__.return_value
        request_client.get = AsyncMock(return_value=response)

        client = StalwartJMAPClient(
            user_email='managed@shizuha.com',
            auth_token='request-token',
        )
        session = __import__('asyncio').run(client.get_session())

        self.assertIn('provider-account', session['accounts'])
        self.assertEqual(client.account_id, 'provider-account')
        async_client_cls.assert_called_once_with(
            timeout=30.0,
            verify=True,
            follow_redirects=True,
        )
