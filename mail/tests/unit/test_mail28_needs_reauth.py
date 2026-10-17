"""MAIL-28: needs_reauth connection status for OAuth token expiry.

OAuth accounts must NOT get auth_failed on IMAP AUTHENTICATIONFAILED —
they get needs_reauth (the user must re-authorize, not fix a password).
Password accounts are unchanged: AUTHENTICATIONFAILED → auth_failed.
"""
import imaplib
from unittest import mock

from django.test import TestCase

from mail.models import MailAccount
from mail.sync_utils import classify_connection_error, PERSISTENT_STATUSES


def _account(email, auth_type='password'):
    return MailAccount.objects.create(
        user_id=1,
        email_address=email,
        incoming_host='imap.example.com',
        outgoing_host='smtp.example.com',
        incoming_username=email,
        auth_type=auth_type,
    )


class ClassifyNeedsReauthTests(TestCase):
    """Unit tests for classify_connection_error with auth_type='oauth2'."""

    def test_oauth_imap_auth_error_returns_needs_reauth(self):
        err = imaplib.IMAP4.error('[AUTHENTICATIONFAILED] Invalid credentials (token expired)')
        self.assertEqual(
            classify_connection_error(err, auth_type='oauth2'),
            'needs_reauth',
        )

    def test_password_imap_auth_error_still_returns_auth_failed(self):
        err = imaplib.IMAP4.error('[AUTHENTICATIONFAILED] Invalid credentials')
        self.assertEqual(
            classify_connection_error(err, auth_type='password'),
            'auth_failed',
        )

    def test_oauth_message_path_auth_returns_needs_reauth(self):
        self.assertEqual(
            classify_connection_error(message='AUTH failed', auth_type='oauth2'),
            'needs_reauth',
        )

    def test_password_message_path_auth_unchanged(self):
        self.assertEqual(
            classify_connection_error(message='AUTH failed', auth_type='password'),
            'auth_failed',
        )

    def test_oauth_ssl_error_unchanged(self):
        import ssl
        self.assertEqual(
            classify_connection_error(ssl.SSLError('cert verify failed'), auth_type='oauth2'),
            'ssl_error',
        )

    def test_needs_reauth_is_persistent(self):
        self.assertIn('needs_reauth', PERSISTENT_STATUSES)

    def test_auth_failed_still_persistent(self):
        self.assertIn('auth_failed', PERSISTENT_STATUSES)


class SyncMailboxCountsNeedsReauthTests(TestCase):
    """sync_mailbox_counts sets needs_reauth for OAuth accounts on auth failure."""

    @mock.patch('mail.tasks._is_jmap_account', return_value=False)
    @mock.patch(
        'mail.tasks._sync_imap_folder_counts',
        side_effect=imaplib.IMAP4.error('[AUTHENTICATIONFAILED] token expired'),
    )
    def test_oauth_account_gets_needs_reauth(self, *_):
        acct = _account('m28-counts-oauth@shizuha.com', auth_type='oauth2')
        from mail import tasks
        tasks.sync_mailbox_counts(str(acct.id), expected_user_id=1)
        acct.refresh_from_db()
        self.assertEqual(acct.connection_status, 'needs_reauth')

    @mock.patch('mail.tasks._is_jmap_account', return_value=False)
    @mock.patch(
        'mail.tasks._sync_imap_folder_counts',
        side_effect=imaplib.IMAP4.error('[AUTHENTICATIONFAILED] bad credentials'),
    )
    def test_password_account_gets_auth_failed(self, *_):
        acct = _account('m28-counts-pwd@shizuha.com', auth_type='password')
        from mail import tasks
        tasks.sync_mailbox_counts(str(acct.id), expected_user_id=1)
        acct.refresh_from_db()
        self.assertEqual(acct.connection_status, 'auth_failed')


class SyncNewMessagesNeedsReauthTests(TestCase):
    """sync_new_messages sets needs_reauth for OAuth accounts on auth failure."""

    @mock.patch('mail.tasks._is_jmap_account', return_value=False)
    @mock.patch(
        'mail.tasks._sync_imap_messages',
        side_effect=imaplib.IMAP4.error('[AUTHENTICATIONFAILED] token expired'),
    )
    def test_oauth_account_gets_needs_reauth(self, *_):
        acct = _account('m28-msgs-oauth@shizuha.com', auth_type='oauth2')
        from mail import tasks
        tasks.sync_new_messages(str(acct.id), expected_user_id=1)
        acct.refresh_from_db()
        self.assertEqual(acct.connection_status, 'needs_reauth')

    @mock.patch('mail.tasks._is_jmap_account', return_value=False)
    @mock.patch(
        'mail.tasks._sync_imap_messages',
        side_effect=imaplib.IMAP4.error('[AUTHENTICATIONFAILED] bad credentials'),
    )
    def test_password_account_gets_auth_failed(self, *_):
        acct = _account('m28-msgs-pwd@shizuha.com', auth_type='password')
        from mail import tasks
        tasks.sync_new_messages(str(acct.id), expected_user_id=1)
        acct.refresh_from_db()
        self.assertEqual(acct.connection_status, 'auth_failed')
