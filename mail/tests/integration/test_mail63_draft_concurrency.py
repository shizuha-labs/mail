from mail.tests.active_identity import ActiveIdentityTestCase
from django.test import TestCase
from rest_framework.test import APIClient

from mail.models import MailAccount, MailDraft
from mail.tests.helpers import create_test_jwt


class DraftConcurrencyTests(ActiveIdentityTestCase):
    def setUp(self):
        self.owner = 630001
        self.account = MailAccount.objects.create(
            user_id=self.owner,
            email_address='draft-owner@shizuha.com',
            incoming_host='imap.example.com',
            outgoing_host='smtp.example.com',
            incoming_username='draft-owner@shizuha.com',
        )
        self.draft = MailDraft.objects.create(
            account=self.account,
            to_addresses=['first@example.com'],
            subject='revision one',
            body_text='body one',
            attachments=[{'id': 'attachment-one'}],
        )
        self.client = APIClient()
        token = create_test_jwt(user_id=self.owner, email='draft-owner@shizuha.com')
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
        self.url = f'/api/drafts/{self.draft.id}/'

    def test_two_clients_conflict_retry_and_latest_revision_are_atomic(self):
        client_one = {
            'base_revision': 1,
            'to_addresses': ['new@example.com'],
            'body_text': 'client one body',
            'attachments': [{'id': 'attachment-two'}],
        }
        first = self.client.patch(self.url, client_one, format='json')
        self.assertEqual(first.status_code, 200, first.content)
        self.assertEqual(first.data['revision'], 2)

        stale = self.client.patch(self.url, {
            'base_revision': 1,
            'to_addresses': ['stale@example.com'],
            'body_text': 'stale body',
            'attachments': [],
        }, format='json')
        self.assertEqual(stale.status_code, 409, stale.content)
        self.assertEqual(stale.data['code'], 'draft_revision_conflict')
        self.assertEqual(stale.data['current_revision'], 2)

        self.draft.refresh_from_db()
        self.assertEqual(self.draft.to_addresses, ['new@example.com'])
        self.assertEqual(self.draft.body_text, 'client one body')
        self.assertEqual(self.draft.attachments, [{'id': 'attachment-two'}])

        retry = self.client.patch(self.url, client_one, format='json')
        self.assertEqual(retry.status_code, 200, retry.content)
        self.assertEqual(retry.data['revision'], 2)

        latest = self.client.patch(self.url, {
            'base_revision': 2,
            'subject': 'revision three',
            'attachments': [{'id': 'attachment-three'}],
        }, format='json')
        self.assertEqual(latest.status_code, 200, latest.content)
        self.assertEqual(latest.data['revision'], 3)
        self.draft.refresh_from_db()
        self.assertEqual(self.draft.subject, 'revision three')
        self.assertEqual(self.draft.attachments, [{'id': 'attachment-three'}])
        self.assertEqual(self.draft.body_text, 'client one body')
