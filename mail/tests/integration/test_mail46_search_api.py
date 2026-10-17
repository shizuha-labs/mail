from mail.tests.active_identity import ActiveIdentityTestCase
"""MAIL-46 (MAIL-1 Slice 5): scoped relevance-search API extensions.

Covers the additive params (folders[], thread_id, sort, include_snippets),
the per-hit snippet/score/match_reason fields, total/total_capped, keyset
stability per sort mode, legacy parameter compatibility, and the security
contract that an unowned explicit account scope returns EMPTY (never a 404
that confirms a foreign account id exists).
"""
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from mail.models import MailAccount, MailFolder, MailMessage, MailThread
from mail.tests.helpers import create_test_jwt


class Mail46SearchAPITest(ActiveIdentityTestCase):
    def setUp(self):
        self.client = APIClient()
        self.user_id = 46046
        self.account = MailAccount.objects.create(
            user_id=self.user_id,
            email_address='mail46@shizuha.com',
            incoming_host='imap.example.com',
            outgoing_host='smtp.example.com',
            incoming_username='mail46@shizuha.com',
            is_default=True,
        )
        self.other_account = MailAccount.objects.create(
            user_id=99946,
            email_address='foreign46@shizuha.com',
            incoming_host='imap.example.com',
            outgoing_host='smtp.example.com',
            incoming_username='foreign46@shizuha.com',
        )
        self.inbox = MailFolder.objects.create(account=self.account, name='Inbox', role='inbox', jmap_mailbox_id='mb-inbox')
        self.archive = MailFolder.objects.create(account=self.account, name='Archive', role='archive', jmap_mailbox_id='mb-archive')
        self.spam = MailFolder.objects.create(account=self.account, name='Spam', role='junk', jmap_mailbox_id='mb-spam')
        self.other_folder = MailFolder.objects.create(
            account=self.other_account, name='Inbox', role='inbox')
        token = create_test_jwt(user_id=self.user_id, email=self.account.email_address)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')

    def _message(self, folder, subject, preview, received, account=None, thread=None, **kw):
        return MailMessage.objects.create(
            account=account or self.account,
            folder=folder,
            thread=thread,
            subject=subject,
            from_address=kw.get('from_address', 'sender@example.com'),
            from_name=kw.get('from_name', 'Sender'),
            to_addresses=kw.get('to_addresses', [{'email': 'mail46@shizuha.com'}]),
            cc_addresses=kw.get('cc_addresses', []),
            preview=preview,
            date_received=received,
            is_unread=True,
        )

    def _search(self, **payload):
        return self.client.post('/api/messages/search/', payload, format='json')

    def test_folders_multi_scope_filters_by_name_and_role(self):
        now = timezone.now()
        hit_inbox = self._message(self.inbox, 'Gamma report', 'gamma in inbox', now)
        hit_archive = self._message(self.archive, 'Gamma report', 'gamma archived', now)
        self._message(self.spam, 'Gamma report', 'gamma in spam', now)

        response = self._search(query='Gamma', folders=['Inbox', 'archive'])

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = {row['id'] for row in response.data['results']}
        self.assertEqual(ids, {str(hit_inbox.id), str(hit_archive.id)})

    def test_thread_id_filter_scopes_to_one_thread(self):
        now = timezone.now()
        thread = MailThread.objects.create(account=self.account, subject='Delta thread')
        in_thread = self._message(self.inbox, 'Delta one', 'delta text', now, thread=thread)
        self._message(self.inbox, 'Delta two', 'delta text', now)

        response = self._search(query='Delta', thread_id=str(thread.id))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([row['id'] for row in response.data['results']], [str(in_thread.id)])
        self.assertEqual(response.data['results'][0]['thread_id'], thread.id)

    def test_sort_date_asc_and_desc_with_stable_cursor_pagination(self):
        base = timezone.now() - timezone.timedelta(days=3)
        msgs = [
            self._message(self.inbox, f'Epsilon {i}', 'epsilon body',
                          base + timezone.timedelta(days=i))
            for i in range(3)
        ]

        response = self._search(query='Epsilon', sort='date_asc', page_size=2)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        first_ids = [row['id'] for row in response.data['results']]
        self.assertEqual(first_ids, [str(msgs[0].id), str(msgs[1].id)])
        self.assertTrue(response.data['next_cursor'])

        second = self._search(query='Epsilon', sort='date_asc', page_size=2,
                              cursor=response.data['next_cursor'])
        self.assertEqual([row['id'] for row in second.data['results']], [str(msgs[2].id)])
        self.assertIsNone(second.data['next_cursor'])

        desc = self._search(query='Epsilon', sort='date_desc', page_size=3)
        self.assertEqual([row['id'] for row in desc.data['results']],
                         [str(msgs[2].id), str(msgs[1].id), str(msgs[0].id)])

    def test_cursor_from_other_sort_mode_is_ignored(self):
        base = timezone.now() - timezone.timedelta(days=2)
        for i in range(3):
            self._message(self.inbox, f'Zeta {i}', 'zeta body',
                          base + timezone.timedelta(days=i))

        asc = self._search(query='Zeta', sort='date_asc', page_size=1)
        self.assertTrue(asc.data['next_cursor'])
        # Reusing an asc cursor under desc must not corrupt the keyset —
        # the mismatched cursor is dropped and desc page 1 is returned.
        desc = self._search(query='Zeta', sort='date_desc', page_size=3,
                            cursor=asc.data['next_cursor'])
        self.assertEqual(len(desc.data['results']), 3)

    def test_hits_carry_score_match_reason_snippet_and_total(self):
        now = timezone.now()
        self._message(self.inbox, 'Theta budget', 'the theta budget line', now)

        response = self._search(query='theta')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['total'], 1)
        self.assertFalse(response.data['total_capped'])
        row = response.data['results'][0]
        self.assertIn('score', row)
        self.assertIsInstance(row['score'], float)
        self.assertIn('subject', row['match_reason'])
        self.assertIn('content', row['match_reason'])
        self.assertIn('<mark>', row['snippet'])
        self.assertIn('theta', row['snippet'])

    def test_include_snippets_false_omits_snippet(self):
        self._message(self.inbox, 'Iota memo', 'iota preview', timezone.now())

        response = self._search(query='Iota', include_snippets=False)

        row = response.data['results'][0]
        self.assertNotIn('snippet', row)
        self.assertIn('score', row)
        self.assertIn('match_reason', row)

    def test_snippet_escapes_html_from_message_content(self):
        self._message(self.inbox, 'Kappa alert',
                      'kappa <script>alert(1)</script> body', timezone.now())

        response = self._search(query='kappa')

        snippet = response.data['results'][0]['snippet']
        self.assertNotIn('<script>', snippet)
        # PostgreSQL ts_headline removes source tags; the SQLite fallback
        # escapes them. Both must emit only our trusted highlight markup.
        from html.parser import HTMLParser
        class Tags(HTMLParser):
            def __init__(self):
                super().__init__()
                self.names = []
            def handle_starttag(self, tag, attrs):
                self.names.append((tag, attrs))
        tags = Tags()
        tags.feed(snippet)
        self.assertEqual(tags.names, [('mark', [])])
        from django.db import connection
        if connection.vendor != 'postgresql':
            self.assertIn('&lt;script&gt;', snippet)
        self.assertIn('<mark>', snippet)

    def test_legacy_single_account_and_folder_params_still_work(self):
        now = timezone.now()
        hit = self._message(self.inbox, 'Lambda legacy', 'lambda body', now)
        self._message(self.archive, 'Lambda legacy', 'lambda archived', now)

        response = self._search(q='Lambda', account=str(self.account.id), folder='Inbox')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([row['id'] for row in response.data['results']], [str(hit.id)])

    def test_unowned_account_ids_return_empty_without_existence_leak(self):
        self._message(self.other_folder, 'Mu secret', 'foreign data',
                      timezone.now(), account=self.other_account)

        response = self._search(query='Mu', account_ids=[str(self.other_account.id)])

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['results'], [])
        self.assertEqual(response.data['count'], 0)

    def test_no_cross_user_hit_count_or_snippet_leak_in_unified_scope(self):
        now = timezone.now()
        mine = self._message(self.inbox, 'Nu shared-term', 'nu owned', now)
        self._message(self.other_folder, 'Nu shared-term', 'nu foreign secret',
                      now, account=self.other_account)

        response = self._search(query='Nu', scope='unified')

        self.assertEqual(response.data['total'], 1)
        rows = response.data['results']
        self.assertEqual([row['id'] for row in rows], [str(mine.id)])
        self.assertNotIn('foreign', rows[0]['snippet'])
