"""MAIL-27: Celery autoretry/backoff for transient sync failures.

Tests behaviour observable at the task boundary:
- Transient failure → autoretry fires (verified via mock call-count).
- Auth/SSL failure → no retry (call-count = 1, status = auth_failed).
- All retries exhausted → connection_status = needs_attention.
- Task succeeds on a later retry → connection_status = connected.
"""
import imaplib
import socket
from unittest import mock

from django.test import TestCase

from mail.models import MailAccount


def _account(email):
    return MailAccount.objects.create(
        user_id=1,
        email_address=email,
        incoming_host='imap.example.com',
        outgoing_host='smtp.example.com',
        incoming_username=email,
    )


class SyncMailboxCountsRetryTests(TestCase):
    """sync_mailbox_counts retry/backoff behaviour (MAIL-27)."""

    @mock.patch('mail.tasks._is_jmap_account', return_value=False)
    @mock.patch('mail.tasks._sync_imap_folder_counts', side_effect=socket.timeout('timed out'))
    def test_transient_all_retries_exhausted_sets_needs_attention(self, mock_sync, *_):
        """Transient failure on every attempt (6 total) → needs_attention."""
        from mail import tasks
        acct = _account('m27-counts-exhausted@shizuha.com')
        tasks.sync_mailbox_counts.apply(args=[str(acct.id)], kwargs={'expected_user_id': 1})
        # 1 initial attempt + 5 autoretries = 6 total calls
        self.assertEqual(mock_sync.call_count, 6)
        acct.refresh_from_db()
        self.assertEqual(acct.connection_status, 'needs_attention')

    @mock.patch('mail.tasks._is_jmap_account', return_value=False)
    @mock.patch('mail.tasks._sync_imap_folder_counts', side_effect=[socket.timeout('timed out'), None])
    def test_transient_recovers_on_retry_sets_connected(self, mock_sync, *_):
        """First attempt fails (transient), second (retry) succeeds → connected."""
        from mail import tasks
        acct = _account('m27-counts-recover@shizuha.com')
        tasks.sync_mailbox_counts.apply(args=[str(acct.id)], kwargs={'expected_user_id': 1})
        self.assertEqual(mock_sync.call_count, 2)
        acct.refresh_from_db()
        self.assertEqual(acct.connection_status, 'connected')

    @mock.patch('mail.tasks._is_jmap_account', return_value=False)
    @mock.patch('mail.tasks._sync_imap_folder_counts',
                side_effect=imaplib.IMAP4.error('[AUTHENTICATIONFAILED] bad credentials'))
    def test_auth_failed_no_retry(self, mock_sync, *_):
        """Auth failure does not trigger retry — task runs exactly once."""
        from mail import tasks
        acct = _account('m27-counts-auth@shizuha.com')
        tasks.sync_mailbox_counts.apply(args=[str(acct.id)], kwargs={'expected_user_id': 1})
        self.assertEqual(mock_sync.call_count, 1)
        acct.refresh_from_db()
        self.assertEqual(acct.connection_status, 'auth_failed')

    @mock.patch('mail.tasks._is_jmap_account', return_value=False)
    @mock.patch('mail.tasks._sync_imap_folder_counts', side_effect=socket.timeout('timed out'))
    def test_transient_at_max_retries_sets_needs_attention_immediately(self, mock_sync, *_):
        """At retries=max_retries: exhaustion path fires immediately (no further retry)."""
        from mail import tasks
        acct = _account('m27-counts-preexhausted@shizuha.com')
        tasks.sync_mailbox_counts.apply(args=[str(acct.id)], kwargs={'expected_user_id': 1}, retries=5)
        self.assertEqual(mock_sync.call_count, 1)
        acct.refresh_from_db()
        self.assertEqual(acct.connection_status, 'needs_attention')


class SyncNewMessagesRetryTests(TestCase):
    """sync_new_messages retry/backoff behaviour (MAIL-27)."""

    @mock.patch('mail.tasks._notify_daemon_new_messages')
    @mock.patch('mail.tasks._is_jmap_account', return_value=False)
    @mock.patch('mail.tasks._sync_imap_messages', side_effect=ConnectionError('connection refused'))
    def test_transient_all_retries_exhausted_sets_needs_attention(self, mock_sync, *_):
        """Transient failure on every attempt (6 total) → needs_attention."""
        from mail import tasks
        acct = _account('m27-msgs-exhausted@shizuha.com')
        tasks.sync_new_messages.apply(args=[str(acct.id)], kwargs={'expected_user_id': 1})
        self.assertEqual(mock_sync.call_count, 6)
        acct.refresh_from_db()
        self.assertEqual(acct.connection_status, 'needs_attention')

    @mock.patch('mail.tasks._notify_daemon_new_messages')
    @mock.patch('mail.tasks._is_jmap_account', return_value=False)
    @mock.patch('mail.tasks._sync_imap_messages', side_effect=[ConnectionError('conn refused'), 0])
    def test_transient_recovers_on_retry_sets_connected(self, mock_sync, *_):
        """First attempt fails (transient), retry succeeds → connected."""
        from mail import tasks
        acct = _account('m27-msgs-recover@shizuha.com')
        tasks.sync_new_messages.apply(args=[str(acct.id)], kwargs={'expected_user_id': 1})
        self.assertEqual(mock_sync.call_count, 2)
        acct.refresh_from_db()
        self.assertEqual(acct.connection_status, 'connected')

    @mock.patch('mail.tasks._notify_daemon_new_messages')
    @mock.patch('mail.tasks._is_jmap_account', return_value=False)
    @mock.patch('mail.tasks._sync_imap_messages',
                side_effect=imaplib.IMAP4.error('[AUTHENTICATIONFAILED] bad credentials'))
    def test_auth_failed_no_retry(self, mock_sync, *_):
        """Auth failure is not retried — task runs exactly once."""
        from mail import tasks
        acct = _account('m27-msgs-auth@shizuha.com')
        tasks.sync_new_messages.apply(args=[str(acct.id)], kwargs={'expected_user_id': 1})
        self.assertEqual(mock_sync.call_count, 1)
        acct.refresh_from_db()
        self.assertEqual(acct.connection_status, 'auth_failed')

    @mock.patch('mail.tasks._notify_daemon_new_messages')
    @mock.patch('mail.tasks._is_jmap_account', return_value=False)
    @mock.patch('mail.tasks._sync_imap_messages', side_effect=ConnectionError('connection refused'))
    def test_transient_at_max_retries_sets_needs_attention_immediately(self, mock_sync, *_):
        """At retries=max_retries: exhaustion path fires immediately (no further retry)."""
        from mail import tasks
        acct = _account('m27-msgs-preexhausted@shizuha.com')
        tasks.sync_new_messages.apply(args=[str(acct.id)], kwargs={'expected_user_id': 1}, retries=5)
        self.assertEqual(mock_sync.call_count, 1)
        acct.refresh_from_db()
        self.assertEqual(acct.connection_status, 'needs_attention')
