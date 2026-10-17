from mail.tests.active_identity import ActiveIdentityTestCase
"""MAIL-163: contact quality — dedup, autocomplete ranking, sync from message history, scoping."""

import uuid

from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from mail.models import MailAccount, MailContact, MailFolder, MailMessage, MailThread
from mail.services.contact_service import sync_contacts_from_messages
from mail.tests.helpers import create_test_jwt


def _make_account(user_id, email):
    return MailAccount.objects.create(
        user_id=user_id,
        email_address=email,
        incoming_host='imap.example.com',
        outgoing_host='smtp.example.com',
        incoming_username=email,
    )


def _make_message(account, folder, from_address, from_name='', to_addresses=None, subject='Hi'):
    return MailMessage.objects.create(
        account=account,
        folder=folder,
        thread=MailThread.objects.create(
            account=account,
            jmap_thread_id=f'thread-{uuid.uuid4()}',
        ),
        from_address=from_address,
        from_name=from_name,
        to_addresses=to_addresses or [],
        subject=subject,
        date_sent=timezone.now(),
        date_received=timezone.now(),
    )


class ContactQualityTest(ActiveIdentityTestCase):
    def setUp(self):
        self.user_id = 20001
        self.account = _make_account(self.user_id, 'owner@shizuha.com')
        self.folder = MailFolder.objects.create(
            account=self.account, name='INBOX', role='inbox',
        )
        token = create_test_jwt(user_id=self.user_id, email=self.account.email_address)
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')

    # ── Dedup ──
    def test_create_same_email_dedups(self):
        first = self.client.post('/api/contacts/', {'email': 'Friend@Example.com', 'name': 'Friend'})
        self.assertEqual(first.status_code, status.HTTP_201_CREATED)
        second = self.client.post('/api/contacts/', {'email': 'friend@example.com', 'name': 'Friend Two'})
        # Same normalized email -> not a duplicate row; returns the merged contact.
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertEqual(second.data['email'], 'friend@example.com')
        self.assertEqual(MailContact.objects.filter(email='friend@example.com').count(), 1)
        self.assertEqual(second.data['name'], 'Friend Two')

    def test_dedup_merges_usage_signals(self):
        # The unique (account, email) constraint prevents new duplicates, so
        # simulate legacy pre-constraint data by dropping it, inserting dupes,
        # and verifying the merge rule converges to one row with summed signals.
        from django.db import connection
        from mail.models import MailContact
        with connection.cursor() as cur:
            cur.execute('ALTER TABLE mail_contact DROP CONSTRAINT mail_contact_account_id_email_cedeaea9_uniq')
            cur.execute(
                "INSERT INTO mail_contact (id, account_id, email, name, send_count, receive_count, is_manual, created_at, updated_at) "
                "VALUES (gen_random_uuid(), %s, 'dup@x.com', '', 3, 0, false, now(), now()), "
                "(gen_random_uuid(), %s, 'dup@x.com', '', 0, 4, false, now(), now())",
                [self.account.id, self.account.id],
            )
        from mail.services.contact_service import _merge_duplicate_contacts
        removed = _merge_duplicate_contacts(self.account)
        self.assertEqual(removed, 1)
        remaining = MailContact.objects.get(account=self.account, email='dup@x.com')
        self.assertEqual(remaining.send_count + remaining.receive_count, 7)

    # ── Autocomplete ranking ──
    def test_autocomplete_ranks_by_recency_and_frequency(self):
        recent = MailContact.objects.create(
            account=self.account, email='recent@x.com', name='Recent',
            last_contacted_at=timezone.now(), send_count=1,
        )
        frequent = MailContact.objects.create(
            account=self.account, email='frequent@x.com', name='Frequent',
            last_contacted_at=timezone.now() - timezone.timedelta(days=30),
            send_count=50,
        )
        resp = self.client.get('/api/contacts/autocomplete/', {'q': '@x.com'})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        emails = [c['email'] for c in resp.data]
        # Most recently contacted surfaces first, then most-used.
        self.assertEqual(emails[0], 'recent@x.com')
        self.assertEqual(emails[1], 'frequent@x.com')

    # ── Sync from message history ──
    def test_sync_populates_senders_and_recipients(self):
        _make_message(self.account, self.folder, from_address='Sender@X.com', from_name='Sender')
        _make_message(
            self.account, self.folder,
            from_address='other@x.com', to_addresses=['Recipient@X.com'],
        )
        count = sync_contacts_from_messages(self.account)
        self.assertGreaterEqual(count, 2)
        sender = MailContact.objects.get(account=self.account, email='sender@x.com')
        self.assertEqual(sender.receive_count, 1)
        recipient = MailContact.objects.get(account=self.account, email='recipient@x.com')
        self.assertEqual(recipient.send_count, 1)

    def test_sync_is_idempotent(self):
        _make_message(self.account, self.folder, from_address='Idem@X.com')
        sync_contacts_from_messages(self.account)
        sync_contacts_from_messages(self.account)
        self.assertEqual(MailContact.objects.filter(email='idem@x.com').count(), 1)

    # ── Scoping ──
    def test_autocomplete_scoped_to_own_mailbox(self):
        other_user = _make_account(99999, 'other@shizuha.com')
        MailContact.objects.create(
            account=other_user, email='secret@other.com', name='Secret',
        )
        MailContact.objects.create(
            account=self.account, email='mine@x.com', name='Mine',
        )
        resp = self.client.get('/api/contacts/autocomplete/', {'q': 'secret'})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data, [])
        resp2 = self.client.get('/api/contacts/autocomplete/', {'q': 'mine'})
        self.assertEqual(len(resp2.data), 1)
