from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from mail.models import MailAccount, MailFolder, MailMessage
from mail.services.mail_service import MailService, MailServiceError


class FakeIMAPClient:
    def __init__(self, messages):
        self.messages = messages

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def select_folder(self, _folder_name, readonly=True):
        return {'uidvalidity': 1}

    def fetch_messages(self, folder_name, limit=100, search_criteria='ALL'):
        return self.messages


class Mail11MidSyncRevokeTests(TestCase):
    def setUp(self):
        self.account = MailAccount.objects.create(
            user_id=71001,
            email_address='mail11-owner@shizuha.com',
            incoming_host='imap.example.com',
            outgoing_host='smtp.example.com',
            incoming_username='mail11-owner@shizuha.com',
            is_active=True,
        )
        self.folder = MailFolder.objects.create(
            account=self.account,
            name='INBOX',
            role='inbox',
            jmap_mailbox_id='imap:INBOX',
        )

    def test_imap_sync_rechecks_revocation_after_fetch_before_write(self):
        from mail.tasks import _sync_imap_messages

        fetched = [
            SimpleNamespace(
                uid=1,
                message_id='mail11@example.com',
                subject='Should not persist',
                from_address='sender@example.com',
                from_name='Sender',
                to_addresses=['mail11-owner@shizuha.com'],
                cc_addresses=[],
                preview='revoked',
                size=123,
                is_read=False,
                is_flagged=False,
                has_attachments=False,
                date=timezone.now(),
                in_reply_to='',
                references=[],
            )
        ]

        with patch('mail.tasks._get_imap_client', return_value=FakeIMAPClient(fetched)), \
             patch('mail.tasks._account_is_still_active', side_effect=[True, False]):
            count = _sync_imap_messages(self.account)

        self.assertEqual(count, 0)
        self.assertFalse(MailMessage.objects.filter(account=self.account).exists())


class Mail11InactiveTestConnectionTests(SimpleTestCase):
    def test_inactive_account_test_connection_fails_before_client_setup(self):
        service = MailService()
        inactive_account = SimpleNamespace(is_active=False)

        with patch.object(service, 'create_imap_client') as create_imap, \
             patch.object(service, 'create_jmap_client') as create_jmap, \
             patch.object(service, 'create_smtp_client') as create_smtp:
            with self.assertRaises(MailServiceError):
                service.test_connection(inactive_account)

        create_imap.assert_not_called()
        create_jmap.assert_not_called()
        create_smtp.assert_not_called()
