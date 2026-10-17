from mail.tests.active_identity import ActiveIdentityTestCase
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework import status

from mail.models import MailAccount, MailFolder, MailMessage
from mail.tests.helpers import create_test_jwt


class MailSearchFTSAPITest(ActiveIdentityTestCase):
    def setUp(self):
        self.client = APIClient()
        self.user_id = 17017
        self.account1 = MailAccount.objects.create(
            user_id=self.user_id,
            email_address='search1@shizuha.com',
            incoming_host='imap.example.com',
            outgoing_host='smtp.example.com',
            incoming_username='search1@shizuha.com',
            is_default=True,
        )
        self.account2 = MailAccount.objects.create(
            user_id=self.user_id,
            email_address='search2@shizuha.com',
            incoming_host='imap.example.com',
            outgoing_host='smtp.example.com',
            incoming_username='search2@shizuha.com',
        )
        self.other_account = MailAccount.objects.create(
            user_id=99917,
            email_address='other-search@shizuha.com',
            incoming_host='imap.example.com',
            outgoing_host='smtp.example.com',
            incoming_username='other-search@shizuha.com',
        )
        self.folder1 = MailFolder.objects.create(account=self.account1, name='Inbox', role='inbox')
        self.folder2 = MailFolder.objects.create(account=self.account2, name='Inbox', role='inbox')
        self.other_folder = MailFolder.objects.create(account=self.other_account, name='Inbox', role='inbox')
        token = create_test_jwt(user_id=self.user_id, email=self.account1.email_address)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')

    def _message(self, account, folder, subject, preview, received, **kwargs):
        return MailMessage.objects.create(
            account=account,
            folder=folder,
            subject=subject,
            from_address=kwargs.get('from_address', 'sender@example.com'),
            from_name=kwargs.get('from_name', 'Sender'),
            to_addresses=kwargs.get('to_addresses', [{'email': account.email_address}]),
            cc_addresses=kwargs.get('cc_addresses', []),
            preview=preview,
            date_received=received,
            has_attachments=kwargs.get('has_attachments', False),
            is_unread=kwargs.get('is_unread', True),
        )

    def test_default_search_keeps_legacy_single_account_scope(self):
        now = timezone.now()
        in_default = self._message(self.account1, self.folder1, 'Alpha roadmap', 'quarterly plan', now)
        self._message(self.account2, self.folder2, 'Alpha other account', 'unified only', now)
        self._message(self.other_account, self.other_folder, 'Alpha foreign owner', 'must not leak', now)

        response = self.client.post('/api/messages/search/', {'query': 'Alpha'}, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([row['id'] for row in response.data['results']], [str(in_default.id)])

    def test_search_vector_backfill_restores_fts_visibility(self):
        """PLAT-6826: rows with NULL search_vector are invisible to FTS search;
        the backfill task repopulates them so search→get works again."""
        from django.db import connection
        from mail.tasks import backfill_message_search_vector

        if connection.vendor != 'postgresql':
            self.skipTest('FTS search_vector backfill is Postgres-only')

        now = timezone.now()
        msg = self._message(self.account1, self.folder1, 'Unicorn migration', 'search me please', now)
        # Simulate a pre-trigger row: NULL search_vector. Updating only
        # search_vector does NOT re-fire the BEFORE UPDATE OF <indexed cols>
        # trigger, so it stays NULL.
        MailMessage.objects.filter(pk=msg.pk).update(search_vector=None)

        before = self.client.post('/api/messages/search/', {'query': 'Unicorn'}, format='json')
        self.assertEqual(before.data['count'], 0)

        result = backfill_message_search_vector.apply()
        data = result.result or {}
        self.assertGreaterEqual(data.get('processed', 0), 1)

        after = self.client.post('/api/messages/search/', {'query': 'Unicorn'}, format='json')
        self.assertEqual(after.status_code, status.HTTP_200_OK)
        self.assertEqual([row['id'] for row in after.data['results']], [str(msg.id)])

    def test_backfill_management_command_supports_inline_and_celery_dispatch(self):
        from django.core.management import call_command
        from django.db import connection
        from io import StringIO
        if connection.vendor != 'postgresql':
            self.skipTest('FTS backfill requires PostgreSQL')
        msg = self._message(self.account1, self.folder1, 'Backfill command', 'fixture', timezone.now())
        for arguments in ((), ('--async',)):
            with self.subTest(arguments=arguments):
                MailMessage.objects.filter(pk=msg.pk).update(search_vector=None)
                output = StringIO()
                call_command('backfill_mail_search_vector', *arguments, stdout=output)
                msg.refresh_from_db()
                self.assertIsNotNone(msg.search_vector)
                self.assertIn('Done' if not arguments else 'Dispatched', output.getvalue())

    def test_backfill_failed_batch_preserves_database_transaction(self):
        from django.db import connection
        from mail.tasks import backfill_message_search_vector
        from unittest.mock import patch
        if connection.vendor != 'postgresql':
            self.skipTest('FTS backfill requires PostgreSQL')
        msg = self._message(self.account1, self.folder1, 'Backfill failure', 'fixture', timezone.now())
        MailMessage.objects.filter(pk=msg.pk).update(search_vector=None)
        with patch('mail.tasks.SEARCH_VECTOR_SQL', 'unavailable_column'):
            result = backfill_message_search_vector.apply(throw=True).result
        self.assertEqual(result, {'processed': 0, 'errors': 1, 'total': 1})
        # A failed batch is isolated by a savepoint; the caller can read/retry.
        self.assertTrue(MailMessage.objects.filter(pk=msg.pk, search_vector__isnull=True).exists())
        recovered = backfill_message_search_vector.apply(throw=True).result
        self.assertEqual(recovered, {'processed': 1, 'errors': 0, 'total': 1})

    def test_unified_search_filters_owner_accounts_and_paginates_by_cursor(self):
        older = timezone.now() - timezone.timedelta(days=1)
        newer = timezone.now()
        msg1 = self._message(self.account1, self.folder1, 'Beta launch', 'from default account', newer)
        msg2 = self._message(self.account2, self.folder2, 'Beta launch', 'from second account', older)
        self._message(self.other_account, self.other_folder, 'Beta launch', 'foreign owner', newer)

        first = self.client.post('/api/messages/search/', {'q': 'Beta', 'scope': 'unified', 'page_size': 1}, format='json')
        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertEqual(len(first.data['results']), 1)
        self.assertTrue(first.data['next_cursor'])

        second = self.client.post('/api/messages/search/', {
            'q': 'Beta',
            'scope': 'unified',
            'page_size': 1,
            'cursor': first.data['next_cursor'],
        }, format='json')
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        returned = {first.data['results'][0]['id'], second.data['results'][0]['id']}
        self.assertEqual(returned, {str(msg1.id), str(msg2.id)})

    def test_filters_include_recipient_attachment_and_read_aliases(self):
        now = timezone.now()
        match = self._message(
            self.account1,
            self.folder1,
            'Gamma invoice',
            'attachment metadata search',
            now,
            to_addresses=[{'email': 'finance@shizuha.com'}],
            has_attachments=True,
            is_unread=False,
        )
        self._message(self.account1, self.folder1, 'Gamma invoice', 'wrong recipient', now, has_attachments=True, is_unread=False)

        response = self.client.post('/api/messages/search/', {
            'q': 'Gamma',
            'to_address': 'finance@shizuha.com',
            'has_attachments': True,
            'is_read': True,
        }, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([row['id'] for row in response.data['results']], [str(match.id)])
