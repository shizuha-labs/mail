"""MAIL-24: sync error classification (auth/ssl/transient) + last_sync_at.

Covers the shared classifier (exception + message paths) and the sync-task behaviour:
last_sync_at written on success, correct classification on failure, persistent
(auth/ssl) not re-raised vs transient re-raised.
"""
import imaplib
import socket
import ssl
from unittest import mock

from django.test import TestCase

from mail.models import MailAccount
from mail.sync_utils import classify_connection_error, PERSISTENT_STATUSES, TransientSyncError


def _account(email='m24@shizuha.com'):
    return MailAccount.objects.create(
        user_id=1,
        email_address=email,
        incoming_host='imap.example.com',
        outgoing_host='smtp.example.com',
        incoming_username=email,
    )


class ClassifyConnectionErrorTests(TestCase):
    def test_imap_auth_exception(self):
        self.assertEqual(
            classify_connection_error(imaplib.IMAP4.error('[AUTHENTICATIONFAILED] Invalid credentials')),
            'auth_failed',
        )

    def test_ssl_error_by_type(self):
        self.assertEqual(classify_connection_error(ssl.SSLError('certificate verify failed')), 'ssl_error')

    def test_timeout_and_reset_are_transient(self):
        self.assertEqual(classify_connection_error(socket.timeout('timed out')), 'connection_failed')
        self.assertEqual(classify_connection_error(ConnectionResetError('reset by peer')), 'connection_failed')

    def test_message_path_matches_legacy_test_connection(self):
        # test_connection passes message=; auth checked before ssl, else connection_failed.
        self.assertEqual(classify_connection_error(message='AUTH failed'), 'auth_failed')
        self.assertEqual(classify_connection_error(message='SSL handshake failed'), 'ssl_error')
        self.assertEqual(classify_connection_error(message='connection refused'), 'connection_failed')

    def test_persistent_set(self):
        self.assertEqual(PERSISTENT_STATUSES, frozenset({
            'auth_failed', 'ssl_error', 'needs_reauth',
        }))


class SyncNewMessagesStatusTests(TestCase):
    @mock.patch('mail.tasks._notify_daemon_new_messages')
    @mock.patch('mail.tasks._is_jmap_account', return_value=False)
    @mock.patch('mail.tasks._sync_imap_messages', return_value=2)
    def test_success_sets_last_sync_at(self, *_):
        acct = _account()
        self.assertIsNone(acct.last_sync_at)
        from mail import tasks
        tasks.sync_new_messages(str(acct.id), expected_user_id=1)
        acct.refresh_from_db()
        self.assertEqual(acct.connection_status, 'connected')
        self.assertIsNotNone(acct.last_sync_at)

    @mock.patch('mail.tasks._is_jmap_account', return_value=False)
    @mock.patch('mail.tasks._sync_imap_messages',
                side_effect=imaplib.IMAP4.error('[AUTHENTICATIONFAILED] bad'))
    def test_auth_failure_classified_not_raised(self, *_):
        acct = _account()
        from mail import tasks
        # persistent auth failure → surfaced via status, NOT re-raised
        tasks.sync_new_messages(str(acct.id), expected_user_id=1)
        acct.refresh_from_db()
        self.assertEqual(acct.connection_status, 'auth_failed')

    @mock.patch('mail.tasks._is_jmap_account', return_value=False)
    @mock.patch('mail.tasks._sync_imap_messages', side_effect=socket.timeout('timed out'))
    def test_transient_failure_raises_transient_sync_error(self, *_):
        acct = _account()
        from mail import tasks
        # MAIL-27: transient failures now raise TransientSyncError (not the raw exception)
        # so Celery's autoretry_for can intercept and schedule a retry.
        with self.assertRaises(TransientSyncError):
            tasks.sync_new_messages(str(acct.id), expected_user_id=1)
        acct.refresh_from_db()
        self.assertEqual(acct.connection_status, 'connection_failed')


class SyncMailboxCountsStatusTests(TestCase):
    @mock.patch('mail.tasks._is_jmap_account', return_value=False)
    @mock.patch('mail.tasks._sync_imap_folder_counts')
    def test_success_sets_last_sync_at(self, *_):
        acct = _account('m26-counts@shizuha.com')
        self.assertIsNone(acct.last_sync_at)
        from mail import tasks
        tasks.sync_mailbox_counts(str(acct.id), expected_user_id=1)
        acct.refresh_from_db()
        self.assertEqual(acct.connection_status, 'connected')
        self.assertIsNotNone(acct.last_sync_at)

    @mock.patch('mail.tasks._is_jmap_account', return_value=False)
    @mock.patch('mail.tasks._sync_imap_folder_counts',
                side_effect=imaplib.IMAP4.error('[AUTHENTICATIONFAILED] bad'))
    def test_auth_failure_classified_not_raised(self, *_):
        acct = _account('m26-counts-auth@shizuha.com')
        from mail import tasks
        # persistent auth failure → surfaced via status, NOT re-raised
        tasks.sync_mailbox_counts(str(acct.id), expected_user_id=1)
        acct.refresh_from_db()
        self.assertEqual(acct.connection_status, 'auth_failed')

    @mock.patch('mail.tasks._is_jmap_account', return_value=False)
    @mock.patch('mail.tasks._sync_imap_folder_counts', side_effect=socket.timeout('timed out'))
    def test_transient_failure_classified_and_raised(self, *_):
        acct = _account('m26-counts-transient@shizuha.com')
        from mail import tasks
        with self.assertRaises(TransientSyncError):
            tasks.sync_mailbox_counts(str(acct.id), expected_user_id=1)
        acct.refresh_from_db()
        self.assertEqual(acct.connection_status, 'connection_failed')
