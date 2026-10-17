"""MAIL-55: filters must actually fire; custom folders must be fetchable.

Regressions for the WIKI-27 CVQA-L7 journey: create folder -> create filter ->
deliver matching message -> the message lands in the target folder (moved +
marked read), and fetching messages by the local folder UUID never 500s.
"""
from unittest.mock import AsyncMock, MagicMock, patch

from mail.tests.active_identity import ActiveIdentityTestCase as TestCase

from mail.models import MailAccount, MailFilter, MailFolder
from mail.services.filter_engine import (
    apply_filters_to_messages, message_matches, plan_actions,
    preview_filters_for_messages, resolve_message_plan,
)

SENT_SUBJECT = 'WIKI-27 FILTER HIT 20260705'


def _account(**overrides):
    defaults = dict(
        user_id=1,
        email_address='m55@shizuha.com',
        incoming_server_type='jmap',
        incoming_host='stalwart.example.com',
        incoming_port=8080,
        incoming_username='m55@shizuha.com',
        outgoing_host='stalwart.example.com',
        is_active=True,
    )
    defaults.update(overrides)
    return MailAccount.objects.create(**defaults)


def _folder(account, name='cvqa-filter-target', jmap_id='mb-target', role='custom'):
    return MailFolder.objects.create(
        account=account, name=name, role=role, jmap_mailbox_id=jmap_id,
    )


def _filter(account, folder, subject_term='WIKI-27 FILTER HIT'):
    return MailFilter.objects.create(
        account=account,
        name='wiki27',
        match_type='all',
        conditions=[{'field': 'subject', 'operator': 'contains', 'value': subject_term}],
        actions=[{'type': 'move', 'folder_id': str(folder.id)}, {'type': 'mark_read'}],
    )


class FilterMatchingTests(TestCase):
    def setUp(self):
        self.account = _account()
        self.folder = _folder(self.account)
        self.filter = _filter(self.account, self.folder)

    def test_subject_contains_matches_case_insensitively(self):
        self.assertTrue(message_matches(self.filter, {'subject': SENT_SUBJECT.lower()}))
        self.assertFalse(message_matches(self.filter, {'subject': 'unrelated'}))

    def test_match_type_any_and_from_field(self):
        rule = MailFilter.objects.create(
            account=self.account, name='any', match_type='any',
            conditions=[
                {'field': 'subject', 'operator': 'contains', 'value': 'nope'},
                {'field': 'from', 'operator': 'contains', 'value': 'zen@'},
            ],
            actions=[{'type': 'mark_read'}],
        )
        msg = {'subject': 'x', 'from_address': {'email': 'zen@shizuha.com', 'name': 'Zen'}}
        self.assertTrue(message_matches(rule, msg))

    def test_unknown_operator_fails_closed(self):
        rule = MailFilter.objects.create(
            account=self.account, name='weird', match_type='all',
            conditions=[{'field': 'subject', 'operator': 'regex', 'value': '.*'}],
            actions=[{'type': 'mark_read'}],
        )
        self.assertFalse(message_matches(rule, {'subject': 'anything'}))

    def test_plan_actions_normalizes_shapes(self):
        plan = plan_actions(self.filter)
        self.assertEqual(plan['move_folder_ref'], str(self.folder.id))
        self.assertTrue(plan['mark_read'])
        self.assertFalse(plan['delete'])


class FilterApplicationTests(TestCase):
    def setUp(self):
        self.account = _account()
        self.folder = _folder(self.account)
        self.filter = _filter(self.account, self.folder)
        self.client = MagicMock()
        self.client.move_emails = AsyncMock(return_value=True)
        self.client.set_email_keywords = AsyncMock(return_value=True)

    def test_matching_message_is_moved_and_marked_read(self):
        applied = apply_filters_to_messages(
            self.account,
            [{'id': 'em-1', 'subject': SENT_SUBJECT}],
            source_mailbox_id='mb-inbox',
            client=self.client,
        )
        self.assertIn('em-1', applied)
        self.assertEqual(applied['em-1']['moved_to'].id, self.folder.id)
        self.assertTrue(applied['em-1']['mark_read'])
        self.client.move_emails.assert_called_once_with(['em-1'], 'mb-target', 'mb-inbox')
        self.client.set_email_keywords.assert_called_once_with(
            ['em-1'], is_unread=False, is_flagged=None, labels=None,
        )

    def test_non_matching_message_untouched(self):
        applied = apply_filters_to_messages(
            self.account,
            [{'id': 'em-2', 'subject': 'no hit here'}],
            source_mailbox_id='mb-inbox',
            client=self.client,
        )
        self.assertEqual(applied, {})
        self.client.move_emails.assert_not_called()

    def test_inactive_filter_does_not_fire(self):
        self.filter.is_active = False
        self.filter.save(update_fields=['is_active'])
        applied = apply_filters_to_messages(
            self.account,
            [{'id': 'em-3', 'subject': SENT_SUBJECT}],
            source_mailbox_id='mb-inbox',
            client=self.client,
        )
        self.assertEqual(applied, {})

    def test_target_folder_without_mailbox_self_heals_via_create(self):
        self.folder.jmap_mailbox_id = ''
        self.folder.save(update_fields=['jmap_mailbox_id'])
        self.client.create_mailbox = AsyncMock(return_value={'id': 'mb-created'})
        applied = apply_filters_to_messages(
            self.account,
            [{'id': 'em-4', 'subject': SENT_SUBJECT}],
            source_mailbox_id='mb-inbox',
            client=self.client,
        )
        self.folder.refresh_from_db()
        self.assertEqual(self.folder.jmap_mailbox_id, 'mb-created')
        self.assertEqual(applied['em-4']['moved_to'].id, self.folder.id)
        self.client.move_emails.assert_called_once_with(['em-4'], 'mb-created', 'mb-inbox')


class SyncTaskFilterTests(TestCase):
    """Deliver matching message via the sync path -> lands in target folder."""

    def setUp(self):
        self.account = _account()
        self.inbox = _folder(self.account, name='Inbox', jmap_id='mb-inbox', role='inbox')
        self.target = _folder(self.account)
        self.filter = _filter(self.account, self.target)

    def test_sync_applies_filter_and_updates_local_row(self):
        from mail import tasks as mail_tasks
        from mail.models import MailMessage

        client = MagicMock()
        client.get_emails = AsyncMock(return_value={'emails': {'list': [{
            'id': 'em-sync-1',
            'blobId': 'blob-1',
            'threadId': 'th-1',
            'subject': SENT_SUBJECT,
            'from': [{'email': 'zen@shizuha.com'}],
            'to': [{'email': self.account.email_address}],
            'keywords': {},
            'receivedAt': '2026-07-05T00:00:00Z',
            'sentAt': '2026-07-05T00:00:00Z',
        }]}})
        client.move_emails = AsyncMock(return_value=True)
        client.set_email_keywords = AsyncMock(return_value=True)

        with patch.object(mail_tasks, '_get_stalwart_client', return_value=client):
            new_count = mail_tasks._sync_jmap_messages(self.account, folder_id=str(self.inbox.id))

        self.assertEqual(new_count, 1)
        message = MailMessage.objects.get(jmap_email_id='em-sync-1')
        self.assertEqual(message.folder_id, self.target.id)
        self.assertFalse(message.is_unread)
        client.move_emails.assert_called_once_with(['em-sync-1'], 'mb-target', 'mb-inbox')

    def test_sync_propagates_attachment_state_has_attachment_true(self):
        """MAIL-64 merge-gate P2#1: the periodic-sync caller must forward
        has_attachments + folder context, so an attachment/folder rule can
        match through the real ingestion boundary (not just direct
        message_matches calls with synthetic dicts)."""
        from mail import tasks as mail_tasks
        from mail.models import MailFilter, MailMessage

        MailFilter.objects.create(
            account=self.account, name='attach', match_type='all',
            conditions=[
                {'field': 'has_attachment', 'operator': 'is', 'value': True},
                {'field': 'folder', 'operator': 'equals', 'value': 'inbox'},
            ],
            actions=[{'type': 'move', 'folder_id': str(self.target.id)}],
        )

        client = MagicMock()
        client.get_emails = AsyncMock(return_value={'emails': {'list': [{
            'id': 'em-attach-1',
            'blobId': 'blob-1', 'threadId': 'th-1',
            'subject': 'with attachment',
            'from': [{'email': 'zen@shizuha.com'}],
            'to': [{'email': self.account.email_address}],
            'keywords': {},
            'hasAttachment': True,
            'receivedAt': '2026-07-05T00:00:00Z',
            'sentAt': '2026-07-05T00:00:00Z',
        }]}})
        client.move_emails = AsyncMock(return_value=True)
        client.set_email_keywords = AsyncMock(return_value=True)

        with patch.object(mail_tasks, '_get_stalwart_client', return_value=client):
            new_count = mail_tasks._sync_jmap_messages(self.account, folder_id=str(self.inbox.id))

        self.assertEqual(new_count, 1)
        message = MailMessage.objects.get(jmap_email_id='em-attach-1')
        self.assertEqual(message.folder_id, self.target.id)
        client.move_emails.assert_called_once_with(['em-attach-1'], 'mb-target', 'mb-inbox')

    def test_sync_has_attachment_false_rule_matches_attachmentless(self):
        """MAIL-64 merge-gate P2#2: an explicit boolean False 'has_attachment'
        rule must mean 'no attachments' and match an attachment-less message
        through the real sync caller. Before the fix, value:false collapsed to
        the default want=True, so this rule could never match (and instead
        fired on attachment-bearing messages)."""
        from mail import tasks as mail_tasks
        from mail.models import MailFilter, MailMessage

        MailFilter.objects.create(
            account=self.account, name='noattach', match_type='all',
            conditions=[
                {'field': 'has_attachment', 'operator': 'is', 'value': False},
                {'field': 'folder', 'operator': 'equals', 'value': 'inbox'},
            ],
            actions=[{'type': 'move', 'folder_id': str(self.target.id)}],
        )

        client = MagicMock()
        client.get_emails = AsyncMock(return_value={'emails': {'list': [
            {
                'id': 'em-plain-1', 'blobId': 'blob-1', 'threadId': 'th-1',
                'subject': 'plain note', 'from': [{'email': 'zen@shizuha.com'}],
                'to': [{'email': self.account.email_address}],
                'keywords': {}, 'hasAttachment': False,
                'receivedAt': '2026-07-05T00:00:00Z', 'sentAt': '2026-07-05T00:00:00Z',
            },
            {
                'id': 'em-file-1', 'blobId': 'blob-2', 'threadId': 'th-2',
                'subject': 'has a pdf', 'from': [{'email': 'zen@shizuha.com'}],
                'to': [{'email': self.account.email_address}],
                'keywords': {}, 'hasAttachment': True,
                'receivedAt': '2026-07-06T00:00:00Z', 'sentAt': '2026-07-06T00:00:00Z',
            },
        ]}})
        client.move_emails = AsyncMock(return_value=True)
        client.set_email_keywords = AsyncMock(return_value=True)

        with patch.object(mail_tasks, '_get_stalwart_client', return_value=client):
            new_count = mail_tasks._sync_jmap_messages(self.account, folder_id=str(self.inbox.id))

        self.assertEqual(new_count, 2)
        plain = MailMessage.objects.get(jmap_email_id='em-plain-1')
        withfile = MailMessage.objects.get(jmap_email_id='em-file-1')
        # only the attachment-less message matches the value:false rule
        self.assertEqual(plain.folder_id, self.target.id)
        self.assertEqual(withfile.folder_id, self.inbox.id)
        client.move_emails.assert_called_once_with(['em-plain-1'], 'mb-target', 'mb-inbox')

    def test_sync_folder_rule_does_not_fire_on_non_inbox(self):
        """MAIL-64 merge-gate P2#1: a folder=inbox rule must NOT match a
        message synced into a non-inbox folder (context is per-folder)."""
        from mail import tasks as mail_tasks
        from mail.models import MailFilter, MailMessage

        sent = _folder(self.account, name='Sent', jmap_id='mb-sent', role='sent')
        MailFilter.objects.create(
            account=self.account, name='foldinbox', match_type='all',
            conditions=[{'field': 'folder', 'operator': 'equals', 'value': 'inbox'}],
            actions=[{'type': 'move', 'folder_id': str(self.target.id)}],
        )

        client = MagicMock()
        client.get_emails = AsyncMock(return_value={'emails': {'list': [{
            'id': 'em-sent-1', 'blobId': 'blob-1', 'threadId': 'th-1',
            'subject': 'outgoing', 'from': [{'email': self.account.email_address}],
            'to': [{'email': 'x@shizuha.com'}],
            'keywords': {}, 'hasAttachment': False,
            'receivedAt': '2026-07-05T00:00:00Z', 'sentAt': '2026-07-05T00:00:00Z',
        }]}})
        client.move_emails = AsyncMock(return_value=True)
        client.set_email_keywords = AsyncMock(return_value=True)

        with patch.object(mail_tasks, '_get_stalwart_client', return_value=client):
            # only the Sent folder is synced — the inbox rule must not fire
            mail_tasks._sync_jmap_messages(self.account, folder_id=str(sent.id))

        message = MailMessage.objects.get(jmap_email_id='em-sent-1')
        self.assertEqual(message.folder_id, sent.id)
        client.move_emails.assert_not_called()


class FakeUser:
    id = 4255
    is_authenticated = True


class MessagesByFolderApiTests(TestCase):
    """Custom folders are fetchable by their local UUID without 500s."""

    def setUp(self):
        from rest_framework.test import APIClient
        self.account = _account(user_id=FakeUser.id)
        self.folder = _folder(self.account)
        self.api = APIClient()
        self.api.force_authenticate(user=FakeUser())

    def test_local_folder_uuid_resolves_to_jmap_mailbox_id(self):
        service = MagicMock()
        service.sync_messages.return_value = {'messages': [], 'total': 0}
        with patch('mail.services.mail_service.get_mail_service', return_value=service):
            resp = self.api.get(f'/api/messages/?folder={self.folder.id}')
        self.assertEqual(resp.status_code, 200)
        service.sync_messages.assert_called_once_with(self.account, 'mb-target')

    def test_unsynced_folder_uuid_returns_409_not_500(self):
        self.folder.jmap_mailbox_id = ''
        self.folder.save(update_fields=['jmap_mailbox_id'])
        resp = self.api.get(f'/api/messages/?folder={self.folder.id}')
        self.assertEqual(resp.status_code, 409)
        self.assertIn('not synced', resp.json()['error'])

    def test_inbox_listing_applies_filters_and_hides_moved_message(self):
        _filter(self.account, self.folder)
        service = MagicMock()
        service.sync_folders.return_value = [
            {'role': 'inbox', 'jmap_mailbox_id': 'mb-inbox', 'name': 'Inbox'},
        ]
        service.sync_messages.return_value = {
            'messages': [
                {'jmap_email_id': 'em-hit', 'subject': SENT_SUBJECT,
                 'from_address': 'zen@shizuha.com', 'to_addresses': [], 'preview': '',
                 'is_unread': True, 'is_flagged': False, 'has_attachments': False},
                {'jmap_email_id': 'em-other', 'subject': 'unrelated',
                 'from_address': 'x@shizuha.com', 'to_addresses': [], 'preview': '',
                 'is_unread': True, 'is_flagged': False, 'has_attachments': False},
            ],
            'total': 2,
        }
        client = MagicMock()
        client.move_emails = AsyncMock(return_value=True)
        client.set_email_keywords = AsyncMock(return_value=True)
        with patch('mail.services.mail_service.get_mail_service', return_value=service), \
                patch('mail.tasks._get_stalwart_client', return_value=client):
            resp = self.api.get('/api/messages/?folder_role=inbox')
        self.assertEqual(resp.status_code, 200)
        ids = [m['id'] for m in resp.json()['results']]
        self.assertEqual(ids, ['em-other'])
        client.move_emails.assert_called_once_with(['em-hit'], 'mb-target', 'mb-inbox')

    def test_inbox_listing_propagates_folder_and_attachment_context(self):
        """MAIL-64 merge-gate P2#1: the live-listing caller must forward the
        known folder context (plus has_attachments from the fetch) so a
        folder=inbox + has_attachment rule matches through the real API
        boundary, and an explicit value:false attachment rule can match an
        attachment-less message (P2#2)."""
        from mail.models import MailFilter

        # Folder-context rule: inbox + has attachment -> move to target.
        MailFilter.objects.create(
            account=self.account, name='ctx', match_type='all',
            conditions=[
                {'field': 'folder', 'operator': 'equals', 'value': 'inbox'},
                {'field': 'has_attachment', 'operator': 'is', 'value': True},
            ],
            actions=[{'type': 'move', 'folder_id': str(self.folder.id)}],
        )
        service = MagicMock()
        service.sync_folders.return_value = [
            {'role': 'inbox', 'jmap_mailbox_id': 'mb-inbox', 'name': 'Inbox'},
        ]
        service.sync_messages.return_value = {
            'messages': [
                {'jmap_email_id': 'em-with', 'subject': 'report.pdf',
                 'from_address': 'zen@shizuha.com', 'to_addresses': [], 'preview': '',
                 'is_unread': True, 'is_flagged': False, 'has_attachments': True},
                {'jmap_email_id': 'em-plain', 'subject': 'hello',
                 'from_address': 'x@shizuha.com', 'to_addresses': [], 'preview': '',
                 'is_unread': True, 'is_flagged': False, 'has_attachments': False},
            ],
            'total': 2,
        }
        client = MagicMock()
        client.move_emails = AsyncMock(return_value=True)
        client.set_email_keywords = AsyncMock(return_value=True)
        with patch('mail.services.mail_service.get_mail_service', return_value=service), \
                patch('mail.tasks._get_stalwart_client', return_value=client):
            resp = self.api.get('/api/messages/?folder_role=inbox')
        self.assertEqual(resp.status_code, 200)
        client.move_emails.assert_called_once_with(['em-with'], 'mb-target', 'mb-inbox')
        self.assertEqual(
            [m['id'] for m in resp.json()['results']],
            ['em-plain'],  # em-with moved out of the rendered inbox
        )


class FolderCreateApiTests(TestCase):
    """POST /api/folders/ must create the mailbox on the mail server."""

    def setUp(self):
        from rest_framework.test import APIClient
        self.account = _account(user_id=FakeUser.id)
        self.api = APIClient()
        self.api.force_authenticate(user=FakeUser())

    def test_create_folder_creates_stalwart_mailbox_and_stores_id(self):
        client = MagicMock()
        client.create_mailbox = AsyncMock(return_value={'id': 'mb-new'})
        with patch('mail.tasks._get_stalwart_client', return_value=client):
            resp = self.api.post('/api/folders/', {'name': 'cvqa-new'}, format='json')
        self.assertEqual(resp.status_code, 201)
        folder = MailFolder.objects.get(account=self.account, name='cvqa-new')
        self.assertEqual(folder.jmap_mailbox_id, 'mb-new')

    def test_create_folder_survives_stalwart_failure(self):
        with patch('mail.tasks._get_stalwart_client', side_effect=RuntimeError('down')):
            resp = self.api.post('/api/folders/', {'name': 'cvqa-offline'}, format='json')
        self.assertEqual(resp.status_code, 201)
        folder = MailFolder.objects.get(account=self.account, name='cvqa-offline')
        self.assertEqual(folder.jmap_mailbox_id, '')


class MAIL64RuleEngineTests(TestCase):
    """MAIL-64: typed conditions/actions, deterministic ordering + conflict
    resolution, preview/apply agreement, and idempotent re-run."""

    def setUp(self):
        self.account = _account()
        self.target = _folder(self.account, name='Work', jmap_id='mb-work')
        self.archive = _folder(self.account, name='Archive', jmap_id='mb-archive', role='archive')
        self.trash = _folder(self.account, name='Trash', jmap_id='mb-trash', role='trash')
        self.client = MagicMock()
        self.client.move_emails = AsyncMock(return_value=True)
        self.client.set_email_keywords = AsyncMock(return_value=True)

    def _ordered(self):
        return list(
            MailFilter.objects.filter(account=self.account, is_active=True)
            .order_by('priority', 'name', 'id')
        )

    # ---- typed conditions ----
    def test_sender_recipient_aliases(self):
        rule = MailFilter.objects.create(
            account=self.account, name='alias', match_type='all',
            conditions=[
                {'field': 'sender', 'operator': 'contains', 'value': 'zen@'},
                {'field': 'recipient', 'operator': 'contains', 'value': 'me@'},
            ],
            actions=[{'type': 'flag'}],
        )
        msg = {'from_address': {'email': 'zen@shizuha.com'},
               'to_addresses': [{'email': 'me@shizuha.com'}]}
        self.assertTrue(message_matches(rule, msg))
        self.assertFalse(message_matches(rule, {'from_address': {'email': 'zen@shizuha.com'}}))

    def test_folder_and_has_attachment_conditions(self):
        rule = MailFilter.objects.create(
            account=self.account, name='fa', match_type='all',
            conditions=[
                {'field': 'folder', 'operator': 'equals', 'value': 'inbox'},
                {'field': 'has_attachment', 'operator': 'is', 'value': 'true'},
            ],
            actions=[{'type': 'archive'}],
        )
        self.assertTrue(message_matches(rule, {'folder': 'Inbox', 'has_attachments': True}))
        self.assertFalse(message_matches(rule, {'folder': 'Inbox', 'has_attachments': False}))
        self.assertFalse(message_matches(rule, {'folder': 'Sent', 'has_attachments': True}))

    def test_boolean_false_has_attachment_is_preserved(self):
        """MAIL-64 merge-gate P2#2: an explicit boolean False must be honored
        (not collapsed to the default want=True). A value:false rule means
        'has NO attachments'."""
        rule = MailFilter.objects.create(
            account=self.account, name='noattach', match_type='all',
            conditions=[{'field': 'has_attachment', 'operator': 'is', 'value': False}],
            actions=[{'type': 'archive'}],
        )
        # matches only attachment-less messages
        self.assertTrue(message_matches(rule, {'has_attachments': False}))
        self.assertTrue(message_matches(rule, {'attachments': []}))
        self.assertFalse(message_matches(rule, {'has_attachments': True}))
        self.assertFalse(message_matches(rule, {'attachments': [{'blobId': 'b1'}]}))

    def test_has_attachment_string_false_is_preserved(self):
        """A JSON/string 'false' means 'has no attachments' too (wire tolerance)."""
        rule = MailFilter.objects.create(
            account=self.account, name='noattach-s', match_type='all',
            conditions=[{'field': 'has_attachment', 'operator': 'is', 'value': 'false'}],
            actions=[{'type': 'archive'}],
        )
        self.assertTrue(message_matches(rule, {'has_attachments': False}))
        self.assertFalse(message_matches(rule, {'has_attachments': True}))

    def test_has_attachment_hld_wire_spellings(self):
        """The HLD canonical spelling is `has-attachment`; legacy spellings
        (`has_attachment`, `hasAttachments`) must all normalize identically."""
        for spelling in ('has-attachment', 'has_attachment', 'has_attachments', 'hasAttachments'):
            rule = MailFilter.objects.create(
                account=self.account, name=f'spell-{spelling}', match_type='all',
                conditions=[{'field': spelling, 'operator': 'is', 'value': True}],
                actions=[{'type': 'archive'}],
            )
            self.assertTrue(
                message_matches(rule, {'has_attachments': True}),
                f'spelling {spelling} should match attachment-bearing message',
            )
            self.assertFalse(
                message_matches(rule, {'has_attachments': False}),
                f'spelling {spelling} should not match attachment-less message',
            )
            rule.delete()

    # ---- typed actions ----
    def test_label_and_archive_actions_plan(self):
        rule = MailFilter.objects.create(
            account=self.account, name='la', match_type='any',
            conditions=[{'field': 'subject', 'operator': 'contains', 'value': 'x'}],
            actions=[{'type': 'label', 'value': 'Work'}, {'type': 'archive'}],
        )
        plan = plan_actions(rule)
        self.assertEqual(plan['labels'], ['Work'])
        self.assertTrue(plan['archive'])

    # ---- deterministic ordering + conflict resolution ----
    def test_conflict_resolution_by_precedence_not_order(self):
        # Two non-stop rules match the same message; trash must win over move
        # regardless of which matched first (deterministic precedence).
        MailFilter.objects.create(
            account=self.account, name='b-move', priority=10, stop_processing=False,
            match_type='any',
            conditions=[{'field': 'subject', 'operator': 'contains', 'value': 'hit'}],
            actions=[{'type': 'move', 'folder_id': str(self.target.id)}],
        )
        MailFilter.objects.create(
            account=self.account, name='a-trash', priority=20, stop_processing=False,
            match_type='any',
            conditions=[{'field': 'subject', 'operator': 'contains', 'value': 'hit'}],
            actions=[{'type': 'trash'}],
        )
        plan = resolve_message_plan(self._ordered(), {'id': 'e', 'subject': 'a hit'})
        self.assertEqual(plan['destination'][0], 'trash')
        self.assertEqual([f.name for f in plan['matched']], ['b-move', 'a-trash'])

    def test_stop_processing_first_match_wins(self):
        MailFilter.objects.create(
            account=self.account, name='a-first', priority=1, stop_processing=True,
            match_type='any',
            conditions=[{'field': 'subject', 'operator': 'contains', 'value': 'hit'}],
            actions=[{'type': 'move', 'folder_id': str(self.target.id)}],
        )
        MailFilter.objects.create(
            account=self.account, name='b-second', priority=2, stop_processing=True,
            match_type='any',
            conditions=[{'field': 'subject', 'operator': 'contains', 'value': 'hit'}],
            actions=[{'type': 'trash'}],
        )
        plan = resolve_message_plan(self._ordered(), {'id': 'e', 'subject': 'a hit'})
        self.assertEqual(plan['destination'], ('move', str(self.target.id)))
        self.assertEqual([f.name for f in plan['matched']], ['a-first'])

    # ---- preview / apply agreement ----
    def test_preview_matches_apply_and_does_not_mutate(self):
        MailFilter.objects.create(
            account=self.account, name='p', match_type='all',
            conditions=[{'field': 'subject', 'operator': 'contains', 'value': 'promo'}],
            actions=[
                {'type': 'move', 'folder_id': str(self.target.id)},
                {'type': 'mark_read'}, {'type': 'label', 'value': 'Ads'},
            ],
        )
        msgs = [{'id': 'em-p', 'subject': 'weekly promo'}]
        preview = preview_filters_for_messages(self.account, msgs)
        self.assertEqual(len(preview), 1)
        row = preview[0]
        self.assertEqual(row['actions']['destination'], 'move')
        self.assertEqual(row['actions']['move_to']['id'], str(self.target.id))
        self.assertTrue(row['actions']['mark_read'])
        self.assertEqual(row['actions']['labels'], ['Ads'])
        # dry-run must NOT touch Stalwart
        self.client.move_emails.assert_not_called()
        self.client.set_email_keywords.assert_not_called()
        # applying yields the same destination/labels/flags
        applied = apply_filters_to_messages(self.account, msgs, 'mb-inbox', self.client)
        self.assertEqual(applied['em-p']['moved_to'].id, self.target.id)
        self.assertTrue(applied['em-p']['mark_read'])
        self.assertEqual(applied['em-p']['labels'], ['Ads'])

    # ---- idempotent re-run ----
    def test_idempotent_rerun_dedupes_labels_and_repeats_outcome(self):
        MailFilter.objects.create(
            account=self.account, name='i', match_type='all',
            conditions=[{'field': 'subject', 'operator': 'contains', 'value': 'news'}],
            actions=[
                {'type': 'archive'},
                {'type': 'label', 'value': 'News'}, {'type': 'label', 'value': 'News'},
            ],
        )
        msgs = [{'id': 'em-i', 'subject': 'daily news'}]
        first = apply_filters_to_messages(self.account, msgs, 'mb-inbox', self.client)
        second = apply_filters_to_messages(self.account, msgs, 'mb-inbox', self.client)
        self.assertTrue(first['em-i']['archived'])
        self.assertEqual(first['em-i']['moved_to'].id, self.archive.id)
        self.assertEqual(first['em-i']['labels'], ['News'])          # de-duplicated
        self.assertEqual(first['em-i']['labels'], second['em-i']['labels'])
        self.assertEqual(first['em-i']['archived'], second['em-i']['archived'])
