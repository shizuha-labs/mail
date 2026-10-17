"""MAIL-48 (PLAT-1317 coverage-RED): cross-owner data-isolation invariant suite.

Two mail owners (distinct Shizuha-ID user_ids). Every owner-scoped surface —
accounts, folders, messages, threads, drafts, filters, contacts, sync — must, when
owner A targets owner B's object, return a NON-ENUMERATING 404 (the
account_not_found_response() shape) and never leak B's data. Complements the
existing account-detail + sync cross-owner tests (test_api.py) with the full
per-resource IDOR matrix the audit found missing.

THREAT (PLAT-1317): an external customer reading/listing/syncing/acting on another
owner's mailbox without a grant. Owner scope is server-derived from the JWT
principal (request.user.id) — never a client-supplied account_id. Access-control
only — no credential rotation.
"""
from mail.tests.active_identity import ActiveIdentityTestCase as TestCase
from rest_framework import status
from rest_framework.test import APIClient

from mail.models import (
    MailAccount, MailFolder, MailMessage, MailThread, MailDraft, MailFilter,
    MailContact,
)
from mail.tests.helpers import create_test_jwt


def _account(user_id, email):
    return MailAccount.objects.create(
        user_id=user_id, email_address=email,
        incoming_host='imap.example.com', outgoing_host='smtp.example.com',
        incoming_username=email,
    )


def _folder(account, mid='INBOX'):
    return MailFolder.objects.create(account=account, jmap_mailbox_id=mid, name=mid)


def _message(account, folder, eid):
    return MailMessage.objects.create(
        account=account, folder=folder, jmap_email_id=eid,
        subject='Confidential — owner B only', from_address='sender@example.com',
    )


class CrossOwnerIsolationTests(TestCase):
    """Owner A (auth'd) must never reach owner B's objects — every surface → 404."""

    OWNER_A, OWNER_B = 480001, 480002

    def setUp(self):
        self.client = APIClient()
        # Owner A is the authenticated caller and owns a default account.
        self.a_account = _account(self.OWNER_A, 'owner-a@shizuha.com')
        self.a_folder = _folder(self.a_account)
        token = create_test_jwt(user_id=self.OWNER_A, email='owner-a@shizuha.com')
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')

        # Owner B's objects — A must not be able to touch any of these.
        self.b_account = _account(self.OWNER_B, 'owner-b@shizuha.com')
        self.b_folder = _folder(self.b_account)
        self.b_message = _message(self.b_account, self.b_folder, 'b-email-1')
        self.b_thread = MailThread.objects.create(
            account=self.b_account, jmap_thread_id='b-thread-1', subject='B thread')
        self.b_draft = MailDraft.objects.create(
            account=self.b_account, subject='B draft', body_text='secret draft')
        self.b_filter = MailFilter.objects.create(
            account=self.b_account, name='B filter', conditions=[], actions=[])
        self.b_contact = MailContact.objects.create(
            account=self.b_account, email='contact-b@example.com')

    def _assert_not_found(self, resp, leak=None):
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND,
                         f'expected 404, got {resp.status_code}: {resp.content[:200]}')
        if leak is not None:
            self.assertNotIn(leak, resp.content.decode('utf-8', 'ignore'))

    def _assert_isolated_list(self, resp, leak=None):
        """A list scoped to another owner's account_id must yield NO foreign data.
        Two safe shapes exist in the codebase: a non-enumerating 404
        (resolve_account_for_request) or a 200 with an empty result set
        (get_queryset → none()). Either is isolation-correct; a 200 with B's
        rows is not."""
        body = resp.content.decode('utf-8', 'ignore')
        self.assertIn(resp.status_code, (status.HTTP_200_OK, status.HTTP_404_NOT_FOUND),
                      f'unexpected {resp.status_code}: {body[:200]}')
        if resp.status_code == status.HTTP_200_OK:
            self.assertTrue('"count":0' in body or '"results":[]' in body,
                            f'foreign-account list returned data: {body[:200]}')
        if leak is not None:
            self.assertNotIn(leak, body)

    # ── account ──
    def test_retrieve_foreign_account_404(self):
        self._assert_not_found(self.client.get(f'/api/accounts/{self.b_account.id}/'),
                               leak='owner-b@shizuha.com')

    def test_delete_foreign_account_404_and_survives(self):
        self._assert_not_found(self.client.delete(f'/api/accounts/{self.b_account.id}/'))
        self.assertTrue(MailAccount.objects.filter(id=self.b_account.id).exists())

    def test_list_accounts_excludes_foreign(self):
        resp = self.client.get('/api/accounts/')
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn('owner-b@shizuha.com', resp.content.decode('utf-8', 'ignore'))

    # ── folders ──
    def test_retrieve_foreign_folder_404(self):
        self._assert_not_found(self.client.get(f'/api/folders/{self.b_folder.id}/'))

    def test_list_folders_scoped_to_foreign_account_isolated(self):
        # Client-supplied account_id for another owner must not resolve to B's data.
        self._assert_isolated_list(self.client.get(f'/api/folders/?account_id={self.b_account.id}'))

    # ── messages ──
    # NOTE: message retrieve/delete WITHOUT account_id are bound to the caller's
    # server-resolved account and fetch the body from Stalwart, so they cannot be
    # asserted here without a live mail server (covered by the live probe,
    # acceptance #3). The DB-layer isolation guarantee — a client-supplied
    # cross-owner account_id is rejected before any fetch — IS testable here:
    def test_retrieve_message_with_foreign_account_id_404(self):
        self._assert_not_found(self.client.get(
            f'/api/messages/{self.b_message.id}/?account_id={self.b_account.id}'))

    def test_list_messages_scoped_to_foreign_account_isolated(self):
        self._assert_isolated_list(self.client.get(f'/api/messages/?account_id={self.b_account.id}'))

    # ── threads ──
    def test_retrieve_foreign_thread_404(self):
        self._assert_not_found(self.client.get(f'/api/threads/{self.b_thread.id}/'))

    def test_list_threads_scoped_to_foreign_account_isolated(self):
        self._assert_isolated_list(self.client.get(f'/api/threads/?account_id={self.b_account.id}'))

    # ── drafts ──
    def test_retrieve_foreign_draft_404(self):
        self._assert_not_found(self.client.get(f'/api/drafts/{self.b_draft.id}/'),
                               leak='secret draft')

    def test_delete_foreign_draft_404_and_survives(self):
        self._assert_not_found(self.client.delete(f'/api/drafts/{self.b_draft.id}/'))
        self.assertTrue(MailDraft.objects.filter(id=self.b_draft.id).exists())

    # ── filters ──
    def test_retrieve_foreign_filter_404(self):
        self._assert_not_found(self.client.get(f'/api/filters/{self.b_filter.id}/'))

    def test_delete_foreign_filter_404_and_survives(self):
        self._assert_not_found(self.client.delete(f'/api/filters/{self.b_filter.id}/'))
        self.assertTrue(MailFilter.objects.filter(id=self.b_filter.id).exists())

    # ── contacts ──
    def test_retrieve_foreign_contact_404(self):
        self._assert_not_found(self.client.get(f'/api/contacts/{self.b_contact.id}/'),
                               leak='contact-b@example.com')

    # ── sync ──
    def test_sync_foreign_account_404(self):
        from unittest.mock import patch
        with patch('mail.tasks.sync_mailbox_counts.delay') as counts, \
             patch('mail.tasks.sync_new_messages.delay') as msgs:
            resp = self.client.post('/api/accounts/sync/', {'account_id': str(self.b_account.id)},
                                    format='json')
        self._assert_not_found(resp)
        counts.assert_not_called()
        msgs.assert_not_called()


class OwnerSeesOwnDataControlTests(TestCase):
    """Positive control: the same surfaces DO resolve for the owner — proving the
    404s above are cross-owner denial, not a blanket block."""

    OWNER = 480010

    def setUp(self):
        self.client = APIClient()
        self.account = _account(self.OWNER, 'owner@shizuha.com')
        self.folder = _folder(self.account)
        self.message = _message(self.account, self.folder, 'own-email-1')
        token = create_test_jwt(user_id=self.OWNER, email='owner@shizuha.com')
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')

    def test_owner_retrieves_own_account(self):
        resp = self.client.get(f'/api/accounts/{self.account.id}/')
        self.assertEqual(resp.status_code, 200)

    def test_owner_lists_own_accounts_only(self):
        resp = self.client.get('/api/accounts/')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('owner@shizuha.com', resp.content.decode('utf-8', 'ignore'))
