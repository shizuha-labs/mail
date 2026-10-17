from mail.tests.active_identity import ActiveIdentityTestCase
"""MAIL-158: Every id emitted by mail search must be accepted by mail retrieve.

Regression suite for the MCP/dashboard flow where a user searches for a message
and then opens it. `/api/messages/search/` surfaces the local DB UUID pk
(MailMessage.id), but the detail retrieve previously passed that pk straight to
the mail provider as the JMAP email id / IMAP UID — which the provider cannot
resolve, so opening a search hit always 404'd.

With the fix, `retrieve` resolves a local UUID pk to the row's provider id
(jmap_email_id / imap_uid) before fetching, while still accepting a direct
provider id as before.
"""
from unittest import mock

from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from mail.models import MailAccount, MailFolder, MailMessage
from mail.tests.helpers import create_test_jwt


class Mail158SearchIdRetrieveTest(ActiveIdentityTestCase):
    def setUp(self):
        self.client = APIClient()
        self.user_id = 58158
        self.account = MailAccount.objects.create(
            user_id=self.user_id,
            email_address='mail158@shizuha.com',
            incoming_host='jmap.example.com',
            incoming_port=443,
            incoming_security='ssl',
            incoming_server_type='jmap',
            incoming_username='mail158@shizuha.com',
            is_default=True,
        )
        self.inbox = MailFolder.objects.create(
            account=self.account, name='Inbox', role='inbox',
            jmap_mailbox_id='mb-inbox')
        self.sent = MailFolder.objects.create(
            account=self.account, name='Sent', role='sent',
            jmap_mailbox_id='mb-sent')
        self.msg = MailMessage.objects.create(
            account=self.account, folder=self.inbox,
            jmap_email_id='jmap-server-id-58158',
            subject='Search me', from_address='sender@example.com',
            preview='opened from search',
        )
        self.sent_msg = MailMessage.objects.create(
            account=self.account, folder=self.sent,
            jmap_email_id='jmap-server-id-58159',
            subject='Search me', from_address='mail158@shizuha.com',
            preview='pure in sent',
        )
        token = create_test_jwt(user_id=self.user_id, email=self.account.email_address)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')

    def _stub_service(self):
        """Patch get_mail_service with a fake that records get_message args."""
        fake = mock.Mock()
        fake.get_message.return_value = {
            'id': 'jmap-server-id-58158',
            'subject': 'Search me',
            'from_address': 'sender@example.com',
            'body_text': 'body',
        }
        patcher = mock.patch('mail.services.mail_service.get_mail_service', return_value=fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        return fake

    def test_search_and_get_works_for_inbox_and_sent(self):
        """Production-order journey (MAIL-158 acceptance): a controlled message in
        BOTH Inbox and Sent is searchable, and each search-emitted UUID pk opens
        via retrieve mapped to its own server jmap id."""
        resp = self.client.post(
            '/api/messages/search/', {'query': 'Search'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        ids = sorted(row['id'] for row in resp.data['results'])
        self.assertEqual(ids, sorted([str(self.msg.id), str(self.sent_msg.id)]))

        for local, server_id in (
            (self.msg.id, 'jmap-server-id-58158'),
            (self.sent_msg.id, 'jmap-server-id-58159'),
        ):
            fake = self._stub_service()
            fake.get_message.return_value = {
                'id': server_id, 'subject': 'Search me', 'body_text': 'body',
            }
            resp = self.client.get(f'/api/messages/{local}/')
            self.assertEqual(resp.status_code, status.HTTP_200_OK)
            fake.get_message.assert_called_once()
            _, message_id, _ = fake.get_message.call_args[0]
            self.assertEqual(message_id, server_id,
                             f'UUID pk {local} must resolve to {server_id}, not be passed raw')

    def test_search_emits_uuid_pk(self):
        """Search surfaces local DB UUID pks (the ids the client opens)."""
        resp = self.client.post(
            '/api/messages/search/', {'query': 'Search'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        ids = sorted(row['id'] for row in resp.data['results'])
        self.assertEqual(ids, sorted([str(self.msg.id), str(self.sent_msg.id)]))

    def test_retrieve_resolves_uuid_pk_to_jmap_id(self):
        """Opening the search-emitted UUID pk fetches via the server jmap id."""
        fake = self._stub_service()
        resp = self.client.get(f'/api/messages/{self.msg.id}/')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        # The provider must be called with the JMAP email id, not the UUID pk.
        fake.get_message.assert_called_once()
        _, message_id, _ = fake.get_message.call_args[0]
        self.assertEqual(message_id, 'jmap-server-id-58158')
        self.assertNotEqual(message_id, str(self.msg.id))

    def test_retrieve_still_accepts_direct_provider_id(self):
        """A direct (non-UUID) provider id keeps working unchanged."""
        fake = self._stub_service()
        resp = self.client.get('/api/messages/jmap-server-id-58158/')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        fake.get_message.assert_called_once()
        self.assertEqual(fake.get_message.call_args[0][1], 'jmap-server-id-58158')

    def test_retrieve_searches_by_row_and_returns_not_found_for_unknown_uuid(self):
        """A random UUID pk falls through and the provider 404 isn't bypassed."""
        fake = self._stub_service()
        fake.get_message.return_value = None
        import uuid
        random_id = uuid.uuid4()
        resp = self.client.get(f'/api/messages/{random_id}/')
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)
        # No local row existed — the raw id is passed to the provider and 404s.
        fake.get_message.assert_called_once()
        self.assertEqual(fake.get_message.call_args[0][1], str(random_id))


class Mail158ImapResolveTest(ActiveIdentityTestCase):
    """The same resolve applies to IMAP accounts (local pk → IMAP UID)."""

    def setUp(self):
        self.client = APIClient()
        self.user_id = 58159
        self.account = MailAccount.objects.create(
            user_id=self.user_id,
            email_address='mail158-imap@shizuha.com',
            incoming_host='imap.example.com',
            incoming_port=993,
            incoming_security='ssl',
            incoming_server_type='imap',
            incoming_username='mail158-imap@shizuha.com',
            is_default=True,
        )
        self.inbox = MailFolder.objects.create(
            account=self.account, name='Inbox', role='inbox')
        self.msg = MailMessage.objects.create(
            account=self.account, folder=self.inbox,
            imap_uid='158-imap-uid-7', subject='IMAP search me',
            from_address='sender@example.com',
        )
        token = create_test_jwt(user_id=self.user_id, email=self.account.email_address)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')

    def test_retrieve_resolves_uuid_pk_to_imap_uid(self):
        fake = mock.Mock()
        fake.get_message.return_value = {'id': '158-imap-uid-7'}
        patcher = mock.patch(
            'mail.services.mail_service.get_mail_service', return_value=fake)
        patcher.start()
        self.addCleanup(patcher.stop)

        resp = self.client.get(f'/api/messages/{self.msg.id}/')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        fake.get_message.assert_called_once()
        account, message_id, folder_id = fake.get_message.call_args[0]
        self.assertEqual(message_id, '158-imap-uid-7')
        # IMAP fetch needs a folder — the row's own folder ref (the inbox name)
        # should be supplied when the caller scoped no folder.
        self.assertEqual(folder_id, 'Inbox')
