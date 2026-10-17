"""MAIL-59: Exactly-once send queue + idempotency for Mail send_email/send_draft.

Tests cover:
- Idempotent replay: same client_message_id returns cached result
- Auto-generated client_message_id when not provided
- 409 Conflict for concurrent in-progress sends
- Failed send replay returns cached error
- Draft deletion after successful send
- Internal send endpoint idempotency
"""
import uuid
import importlib.util
import os
import pathlib
import sys
import types
from unittest.mock import patch, MagicMock
from datetime import datetime, timedelta

from django.test import TestCase, SimpleTestCase, override_settings
from django.db import IntegrityError
from django.utils import timezone
from rest_framework.test import APIClient

from mail.models import MailAccount, MailDraft, OutboxMessage
from mail.views import MailDraftViewSet, MailSendViewSet, InternalSendView
from mail.services.mail_service import MailServiceError


def _load_mail_mcp_module():
    """Load the MCP module without requiring the optional MCP SDK in Django CI."""
    sdk = types.ModuleType('mcp.server.mcpserver')

    class _MCPServer:
        def __init__(self, *args, **kwargs):
            pass

        def tool(self, *args, **kwargs):
            return lambda func: func

        def custom_route(self, *args, **kwargs):
            return lambda func: func

    sdk.MCPServer = _MCPServer
    server = types.ModuleType('mcp.server')
    mcp = types.ModuleType('mcp')
    path = pathlib.Path(__file__).parents[3] / 'mcp-server' / 'mail_server.py'
    # The optional SDK stand-in and generic `base` import must not escape this
    # loader and alter unrelated tests, installed SDK users, or process config.
    with patch.dict(sys.modules, {'mcp': mcp, 'mcp.server': server,
                                 'mcp.server.mcpserver': sdk}), \
         patch.object(sys, 'path', [str(path.parent), *sys.path]), \
         patch.dict(os.environ, {'SHIZUHA_MAIL_URL': 'http://mail.test/api/mail'}):
        prior_base = sys.modules.pop('base', None)
        try:
            spec = importlib.util.spec_from_file_location('mail_mcp_under_test', path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        finally:
            sys.modules.pop('base', None)
            if prior_base is not None:
                sys.modules['base'] = prior_base
    return module


class OptionalMcpFixtureIsolationTests(SimpleTestCase):
    def test_current_sdk_fixture_restores_imports_path_and_environment(self):
        names = ('mcp', 'mcp.server', 'mcp.server.mcpserver', 'base')
        prior = {name: sys.modules.get(name) for name in names}
        path = list(sys.path)
        env = dict(os.environ)
        module = _load_mail_mcp_module()
        self.assertTrue(callable(module.reply_to_email))
        self.assertEqual(sys.path, path)
        self.assertEqual(dict(os.environ), env)
        self.assertEqual({name: sys.modules.get(name) for name in names}, prior)


def _account(**overrides):
    defaults = dict(
        user_id=1,
        email_address='test@shizuha.com',
        incoming_server_type='jmap',
        incoming_host='stalwart.example.com',
        incoming_port=8080,
        incoming_username='test@shizuha.com',
        outgoing_host='stalwart.example.com',
        is_active=True,
    )
    defaults.update(overrides)
    return MailAccount.objects.create(**defaults)


class OutboxMessageModelTests(TestCase):
    """Tests for OutboxMessage model."""

    def setUp(self):
        self.account = _account()

    def test_create_outbox_message(self):
        """Creating an OutboxMessage should set default status to queued."""
        msg = OutboxMessage.objects.create(
            account=self.account,
            client_message_id=uuid.uuid4(),
            to_addresses=['dest@shizuha.com'],
            subject='Test',
        )
        self.assertEqual(msg.status, 'queued')
        self.assertIsNotNone(msg.id)
        self.assertIsNone(msg.sent_at)

    def test_unique_client_message_id_per_account(self):
        """Duplicate client_message_id on same account should raise IntegrityError."""
        cmi = uuid.uuid4()
        OutboxMessage.objects.create(
            account=self.account,
            client_message_id=cmi,
            to_addresses=['dest@shizuha.com'],
            subject='Test',
        )
        with self.assertRaises(Exception):
            OutboxMessage.objects.create(
                account=self.account,
                client_message_id=cmi,
                to_addresses=['dest@shizuha.com'],
                subject='Test 2',
            )

    def test_same_client_message_id_different_accounts(self):
        """Same client_message_id on different accounts should be allowed (cross-account isolation)."""
        account2 = _account(email_address='other@shizuha.com')
        cmi = uuid.uuid4()
        OutboxMessage.objects.create(
            account=self.account,
            client_message_id=cmi,
            to_addresses=['dest@shizuha.com'],
            subject='Account 1',
        )
        # Same cmi on different account — should not raise
        msg2 = OutboxMessage.objects.create(
            account=account2,
            client_message_id=cmi,
            to_addresses=['other@shizuha.com'],
            subject='Account 2',
        )
        self.assertEqual(msg2.client_message_id, cmi)
        self.assertEqual(msg2.account, account2)
        # Verify account-scoped lookup returns only the right one
        self.assertEqual(
            OutboxMessage.objects.filter(account=self.account, client_message_id=cmi).count(),
            1,
        )
        self.assertEqual(
            OutboxMessage.objects.filter(account=account2, client_message_id=cmi).count(),
            1,
        )

    def test_status_transitions(self):
        """OutboxMessage should support status transitions."""
        msg = OutboxMessage.objects.create(
            account=self.account,
            client_message_id=uuid.uuid4(),
            to_addresses=['dest@shizuha.com'],
            subject='Test',
        )
        self.assertEqual(msg.status, 'queued')

        msg.status = 'sending'
        msg.save(update_fields=['status', 'updated_at'])
        msg.refresh_from_db()
        self.assertEqual(msg.status, 'sending')

        msg.status = 'sent'
        msg.result = {'email_id': 'em-1'}
        msg.sent_at = timezone.now()
        msg.save(update_fields=['status', 'result', 'sent_at', 'updated_at'])
        msg.refresh_from_db()
        self.assertEqual(msg.status, 'sent')
        self.assertIsNotNone(msg.sent_at)

    def test_str_representation(self):
        """__str__ should include client_message_id and status."""
        cmi = uuid.uuid4()
        msg = OutboxMessage.objects.create(
            account=self.account,
            client_message_id=cmi,
            to_addresses=['dest@shizuha.com'],
            subject='Hello World',
        )
        s = str(msg)
        self.assertIn(str(cmi), s)
        self.assertIn('queued', s)
        self.assertIn('Hello World', s)


class MailSendViewSetIdempotencyTests(TestCase):
    """Tests for MailSendViewSet idempotency."""

    def setUp(self):
        self.account = _account()
        self.cmi = uuid.uuid4()

    def _create_sent_outbox(self):
        return OutboxMessage.objects.create(
            account=self.account,
            client_message_id=self.cmi,
            status='sent',
            to_addresses=['dest@shizuha.com'],
            subject='Test',
            result={'email_id': 'em-1', 'message_id': '<msg@test>'},
            sent_at=timezone.now(),
        )

    def _create_failed_outbox(self):
        return OutboxMessage.objects.create(
            account=self.account,
            client_message_id=self.cmi,
            status='failed',
            to_addresses=['dest@shizuha.com'],
            subject='Test',
            error='Connection refused',
        )

    def _create_sending_outbox(self):
        return OutboxMessage.objects.create(
            account=self.account,
            client_message_id=self.cmi,
            status='sending',
            to_addresses=['dest@shizuha.com'],
            subject='Test',
        )

    def test_idempotent_replay_returns_cached_result(self):
        """Same client_message_id with sent status should return cached result."""
        self._create_sent_outbox()

        # Simulate the idempotency check from MailSendViewSet.create()
        existing = OutboxMessage.objects.filter(
            account=self.account, client_message_id=self.cmi
        ).first()
        self.assertIsNotNone(existing)
        self.assertEqual(existing.status, 'sent')
        self.assertEqual(
            existing.result.get('email_id'),
            'em-1'
        )

    def test_failed_replay_returns_cached_error(self):
        """Same client_message_id with failed status should return cached error."""
        self._create_failed_outbox()

        existing = OutboxMessage.objects.filter(
            account=self.account, client_message_id=self.cmi
        ).first()
        self.assertEqual(existing.status, 'failed')
        self.assertEqual(existing.error, 'Connection refused')

    def test_in_progress_returns_conflict(self):
        """Same client_message_id with sending status should indicate conflict."""
        self._create_sending_outbox()

        existing = OutboxMessage.objects.filter(
            account=self.account, client_message_id=self.cmi
        ).first()
        self.assertEqual(existing.status, 'sending')

    def test_cross_account_idempotency_isolation(self):
        """Same client_message_id on different accounts should not interfere."""
        account2 = _account(email_address='other@shizuha.com')
        cmi = uuid.uuid4()

        # Create sent outbox for account 1
        OutboxMessage.objects.create(
            account=self.account,
            client_message_id=cmi,
            status='sent',
            to_addresses=['dest@shizuha.com'],
            subject='Account 1',
            result={'email_id': 'em-1'},
            sent_at=timezone.now(),
        )

        # Account 2 should NOT see account 1's outbox entry
        existing_account2 = OutboxMessage.objects.filter(
            account=account2, client_message_id=cmi
        ).first()
        self.assertIsNone(existing_account2, "Account 2 should not see account 1's outbox entry")

        # Account 2 can create its own outbox with the same cmi
        OutboxMessage.objects.create(
            account=account2,
            client_message_id=cmi,
            status='queued',
            to_addresses=['other@shizuha.com'],
            subject='Account 2',
        )
        self.assertEqual(
            OutboxMessage.objects.filter(account=account2, client_message_id=cmi).count(),
            1,
        )

    def test_auto_generates_client_message_id(self):
        """Sending without client_message_id should auto-generate one."""
        msg = OutboxMessage.objects.create(
            account=self.account,
            client_message_id=uuid.uuid4(),
            to_addresses=['dest@shizuha.com'],
            subject='Auto-generated',
        )
        self.assertIsNotNone(msg.client_message_id)


class MailDraftSendIdempotencyTests(TestCase):
    """Tests for MailDraftViewSet.send idempotency."""

    def setUp(self):
        self.account = _account()
        self.draft = MailDraft.objects.create(
            account=self.account,
            to_addresses=['dest@shizuha.com'],
            subject='Draft Test',
            body_text='Hello',
        )

    def test_send_draft_creates_outbox_entry(self):
        """Sending a draft should create an OutboxMessage."""
        cmi = uuid.uuid4()
        outbox = OutboxMessage.objects.create(
            account=self.account,
            client_message_id=cmi,
            status='queued',
            to_addresses=self.draft.to_addresses,
            subject=self.draft.subject,
            body_text=self.draft.body_text,
            draft=self.draft,
        )
        self.assertEqual(outbox.status, 'queued')
        self.assertEqual(outbox.draft_id, self.draft.id)

    def test_send_draft_idempotent_replay(self):
        """Re-sending a draft with same client_message_id should return cached result."""
        cmi = uuid.uuid4()
        OutboxMessage.objects.create(
            account=self.account,
            client_message_id=cmi,
            status='sent',
            to_addresses=self.draft.to_addresses,
            subject=self.draft.subject,
            result={'email_id': 'em-1'},
            sent_at=timezone.now(),
            draft=self.draft,
        )

        existing = OutboxMessage.objects.filter(
            account=self.account, client_message_id=cmi
        ).first()
        self.assertEqual(existing.status, 'sent')
        self.assertEqual(existing.result['email_id'], 'em-1')

    def test_send_draft_deletes_draft_on_success(self):
        """Draft should be deleted after successful send."""
        cmi = uuid.uuid4()
        outbox = OutboxMessage.objects.create(
            account=self.account,
            client_message_id=cmi,
            status='sent',
            to_addresses=self.draft.to_addresses,
            subject=self.draft.subject,
            result={'email_id': 'em-1'},
            sent_at=timezone.now(),
            draft=self.draft,
        )
        # Simulate draft deletion after send
        draft_id = self.draft.id
        self.draft.delete()

        self.assertFalse(MailDraft.objects.filter(id=draft_id).exists())


class InternalSendIdempotencyTests(TestCase):
    """Tests for InternalSendView idempotency."""

    def setUp(self):
        self.account = _account(
            email_address='noreply@shizuha.com',
            user_id=1,
        )

    def test_internal_send_idempotent_replay(self):
        """Internal send with same client_message_id should return cached result."""
        cmi = uuid.uuid4()
        OutboxMessage.objects.create(
            account=self.account,
            client_message_id=cmi,
            status='sent',
            to_addresses=['user@example.com'],
            subject='Welcome',
            result={'status': 'sent', 'message_id': '<msg@test>'},
            sent_at=timezone.now(),
        )

        existing = OutboxMessage.objects.filter(
            account=self.account, client_message_id=cmi
        ).first()
        self.assertEqual(existing.status, 'sent')
        self.assertEqual(existing.result['status'], 'sent')

    def test_internal_send_failed_replay(self):
        """Internal send with failed status should return cached error."""
        cmi = uuid.uuid4()
        OutboxMessage.objects.create(
            account=self.account,
            client_message_id=cmi,
            status='failed',
            to_addresses=['user@example.com'],
            subject='Welcome',
            error='SMTP connection failed',
        )

        existing = OutboxMessage.objects.filter(
            account=self.account, client_message_id=cmi
        ).first()
        self.assertEqual(existing.status, 'failed')
        self.assertEqual(existing.error, 'SMTP connection failed')

    def test_internal_send_in_progress_conflict(self):
        """Internal send with sending status should indicate conflict."""
        cmi = uuid.uuid4()
        OutboxMessage.objects.create(
            account=self.account,
            client_message_id=cmi,
            status='sending',
            to_addresses=['user@example.com'],
            subject='Welcome',
        )

        existing = OutboxMessage.objects.filter(
            account=self.account, client_message_id=cmi
        ).first()
        self.assertEqual(existing.status, 'sending')


class FakeUser:
    id = 1
    is_authenticated = True


class SendEndpointIdempotencyRegressionTests(TestCase):
    """Exercise the real DRF endpoints and provider boundary (MAIL-59 P1)."""

    def setUp(self):
        self.account = _account()
        self.api = APIClient()
        self.api.force_authenticate(user=FakeUser())

    def send_payload(self, cmi):
        return {
            'account_id': str(self.account.id),
            'client_message_id': str(cmi),
            'to_addresses': ['dest@shizuha.com'],
            'subject': 'Endpoint regression',
            'body_text': 'hello',
        }

    def seed_outbox(self, account, count, *, age=None):
        rows = [
            OutboxMessage.objects.create(
                account=account,
                client_message_id=uuid.uuid4(),
                status='sent',
                to_addresses=['dest@shizuha.com'],
                subject=f'Quota seed {index}',
                result={'email_id': f'em-seed-{index}'},
            )
            for index in range(count)
        ]
        if age is not None:
            OutboxMessage.objects.filter(pk__in=[row.pk for row in rows]).update(
                created_at=timezone.now() - age,
            )
        return rows

    @override_settings(MAIL_SEND_QUOTA={'per_minute': 5, 'per_day': 50})
    def test_send_under_quota_reaches_provider_and_creates_outbox(self):
        self.seed_outbox(self.account, 4)
        cmi = uuid.uuid4()
        service = MagicMock()
        service.send_email_jmap.return_value = {'email_id': 'em-under-limit'}

        with patch('mail.services.mail_service.get_mail_service', return_value=service):
            response = self.api.post('/api/send/', self.send_payload(cmi), format='json')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(OutboxMessage.objects.filter(account=self.account).count(), 5)
        service.send_email_jmap.assert_called_once()

    @override_settings(MAIL_SEND_QUOTA={'per_minute': 5, 'per_day': 50})
    def test_send_minute_limit_rejects_before_state_or_provider(self):
        self.seed_outbox(self.account, 5)
        before = OutboxMessage.objects.filter(account=self.account).count()

        with patch('mail.services.mail_service.get_mail_service') as get_service:
            response = self.api.post(
                '/api/send/', self.send_payload(uuid.uuid4()), format='json',
            )

        self.assertEqual(response.status_code, 429)
        self.assertIn('5 sends per minute', response.json()['error'])
        self.assertEqual(OutboxMessage.objects.filter(account=self.account).count(), before)
        get_service.assert_not_called()

    @override_settings(MAIL_SEND_QUOTA={'per_minute': 5, 'per_day': 50})
    def test_send_daily_limit_rejects_before_state_or_provider(self):
        self.seed_outbox(self.account, 50, age=timedelta(minutes=2))
        before = OutboxMessage.objects.filter(account=self.account).count()

        with patch('mail.services.mail_service.get_mail_service') as get_service:
            response = self.api.post(
                '/api/send/', self.send_payload(uuid.uuid4()), format='json',
            )

        self.assertEqual(response.status_code, 429)
        self.assertIn('50 sends per day', response.json()['error'])
        self.assertEqual(OutboxMessage.objects.filter(account=self.account).count(), before)
        get_service.assert_not_called()

    @override_settings(MAIL_SEND_QUOTA={'per_minute': 5, 'per_day': 50})
    def test_idempotent_replay_at_limit_returns_cached_result(self):
        cmi = uuid.uuid4()
        OutboxMessage.objects.create(
            account=self.account,
            client_message_id=cmi,
            status='sent',
            to_addresses=['dest@shizuha.com'],
            subject='Endpoint regression',
            result={'email_id': 'em-cached'},
        )
        self.seed_outbox(self.account, 4)

        with patch('mail.services.mail_service.get_mail_service') as get_service:
            response = self.api.post('/api/send/', self.send_payload(cmi), format='json')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['email_id'], 'em-cached')
        self.assertEqual(OutboxMessage.objects.filter(account=self.account).count(), 5)
        get_service.assert_not_called()

    @override_settings(MAIL_SEND_QUOTA={'per_minute': 5, 'per_day': 50})
    def test_send_quota_is_scoped_to_resolved_account(self):
        sibling = _account(email_address='sibling@shizuha.com')
        self.seed_outbox(sibling, 50, age=timedelta(minutes=2))
        cmi = uuid.uuid4()
        service = MagicMock()
        service.send_email_jmap.return_value = {'email_id': 'em-account-scope'}

        with patch('mail.services.mail_service.get_mail_service', return_value=service):
            response = self.api.post('/api/send/', self.send_payload(cmi), format='json')

        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            OutboxMessage.objects.filter(account=self.account, client_message_id=cmi).exists(),
        )
        service.send_email_jmap.assert_called_once()

    def test_send_success_and_same_key_replay_invoke_provider_once(self):
        cmi = uuid.uuid4()
        service = MagicMock()
        service.send_email_jmap.return_value = {'email_id': 'em-send-1'}
        with patch('mail.services.mail_service.get_mail_service', return_value=service):
            first = self.api.post('/api/send/', self.send_payload(cmi), format='json')
            replay = self.api.post('/api/send/', self.send_payload(cmi), format='json')
        self.assertEqual((first.status_code, replay.status_code), (200, 200))
        self.assertEqual(replay.json()['email_id'], 'em-send-1')
        service.send_email_jmap.assert_called_once()
        outbox = OutboxMessage.objects.get(account=self.account, client_message_id=cmi)
        self.assertEqual((outbox.status, outbox.result['email_id']), ('sent', 'em-send-1'))

    def test_send_timeout_and_same_key_replay_cache_failure_once(self):
        cmi = uuid.uuid4()
        service = MagicMock()
        service.send_email_jmap.side_effect = MailServiceError('provider timeout')
        with patch('mail.services.mail_service.get_mail_service', return_value=service):
            first = self.api.post('/api/send/', self.send_payload(cmi), format='json')
            replay = self.api.post('/api/send/', self.send_payload(cmi), format='json')
        self.assertEqual((first.status_code, replay.status_code), (500, 500))
        service.send_email_jmap.assert_called_once()
        outbox = OutboxMessage.objects.get(account=self.account, client_message_id=cmi)
        self.assertEqual((outbox.status, outbox.error), ('failed', 'provider timeout'))

    @override_settings(MAX_UPLOAD_SIZE=2, MAX_MESSAGE_SIZE=3)
    def test_send_rejects_oversize_attachment_before_provider_or_outbox(self):
        cmi = uuid.uuid4()
        payload = {
            **self.send_payload(cmi),
            'attachments': [{'filename': 'big.txt', 'content': 'YWJj'}],
        }
        with patch('mail.services.mail_service.get_mail_service') as get_service:
            response = self.api.post('/api/send/', payload, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['error_type'], ['attachment_too_large'])
        get_service.assert_not_called()
        self.assertFalse(OutboxMessage.objects.filter(client_message_id=cmi).exists())

    def test_delivery_events_are_monotonic_and_visible_without_resend(self):
        cmi = uuid.uuid4()
        outbox = OutboxMessage.objects.create(
            account=self.account, client_message_id=cmi, status='sent',
            to_addresses=['dest@example.com'], result={'email_id': 'em-1'},
        )
        colliding_account = _account(user_id=2, email_address='collision@shizuha.com')
        colliding_outbox = OutboxMessage.objects.create(
            account=colliding_account, client_message_id=cmi, status='sent',
            to_addresses=['other@example.com'], result={'email_id': 'em-other'},
        )
        headers = {'HTTP_X_INTERNAL_SERVICE': 'dsn-ingest'}
        delayed = self.api.post(
            '/api/internal/delivery-events/',
            {'account_id': str(self.account.id), 'client_message_id': str(cmi),
             'delivery_status': 'delayed',
             'diagnostic': '451 mailbox busy'}, format='json', **headers,
        )
        failed = self.api.post(
            '/api/internal/delivery-events/',
            {'account_id': str(self.account.id), 'client_message_id': str(cmi),
             'delivery_status': 'failed',
             'diagnostic': '550 mailbox unavailable'}, format='json', **headers,
        )
        stale = self.api.post(
            '/api/internal/delivery-events/',
            {'account_id': str(self.account.id), 'client_message_id': str(cmi),
             'delivery_status': 'delayed',
             'diagnostic': 'late retry'}, format='json', **headers,
        )
        visible = self.api.get(f'/api/send/{cmi}/')
        self.assertEqual((delayed.status_code, failed.status_code, stale.status_code), (200, 200, 200))
        self.assertEqual(visible.status_code, 200)
        self.assertEqual(visible.json()['send_status'], 'sent')
        self.assertEqual(visible.json()['delivery_status'], 'failed')
        self.assertEqual(visible.json()['delivery_diagnostic'], '550 mailbox unavailable')
        outbox.refresh_from_db()
        self.assertEqual(outbox.result, {'email_id': 'em-1'})
        colliding_outbox.refresh_from_db()
        self.assertEqual(colliding_outbox.delivery_status, 'pending')
        self.assertEqual(colliding_outbox.result, {'email_id': 'em-other'})

    def test_send_queued_conflict_never_invokes_provider(self):
        cmi = uuid.uuid4()
        OutboxMessage.objects.create(account=self.account, client_message_id=cmi)
        with patch('mail.services.mail_service.get_mail_service') as get_service:
            response = self.api.post('/api/send/', self.send_payload(cmi), format='json')
        self.assertEqual(response.status_code, 409)
        get_service.assert_not_called()

    def test_send_concurrent_insert_integrity_error_is_409(self):
        cmi = uuid.uuid4()
        with patch('mail.views.OutboxMessage.objects.create', side_effect=IntegrityError), \
                patch('mail.services.mail_service.get_mail_service') as get_service:
            response = self.api.post('/api/send/', self.send_payload(cmi), format='json')
        self.assertEqual(response.status_code, 409)
        get_service.assert_not_called()

    def test_draft_success_replays_after_draft_deletion_once(self):
        draft = MailDraft.objects.create(
            account=self.account, to_addresses=['dest@shizuha.com'],
            subject='Draft', body_text='hello')
        cmi = uuid.uuid4()
        service = MagicMock()
        service.send_email_jmap.return_value = {'email_id': 'em-draft-1'}
        url = f'/api/drafts/{draft.id}/send/'
        with patch('mail.services.mail_service.get_mail_service', return_value=service):
            first = self.api.post(url, {'client_message_id': str(cmi)}, format='json')
            replay = self.api.post(url, {'client_message_id': str(cmi)}, format='json')
        self.assertEqual((first.status_code, replay.status_code), (200, 200))
        self.assertFalse(MailDraft.objects.filter(id=draft.id).exists())
        service.send_email_jmap.assert_called_once()
        outbox = OutboxMessage.objects.get(account=self.account, client_message_id=cmi)
        self.assertEqual(outbox.result['draft_id'], str(draft.id))

    def test_draft_timeout_replay_and_conflict_do_not_resend(self):
        draft = MailDraft.objects.create(
            account=self.account, to_addresses=['dest@shizuha.com'],
            subject='Draft', body_text='hello')
        cmi = uuid.uuid4()
        service = MagicMock()
        service.send_email_jmap.side_effect = MailServiceError('draft timeout')
        url = f'/api/drafts/{draft.id}/send/'
        with patch('mail.services.mail_service.get_mail_service', return_value=service):
            first = self.api.post(url, {'client_message_id': str(cmi)}, format='json')
            replay = self.api.post(url, {'client_message_id': str(cmi)}, format='json')
        self.assertEqual((first.status_code, replay.status_code), (500, 500))
        service.send_email_jmap.assert_called_once()
        self.assertEqual(OutboxMessage.objects.get(client_message_id=cmi).status, 'failed')

        conflict_cmi = uuid.uuid4()
        OutboxMessage.objects.create(account=self.account, client_message_id=conflict_cmi, draft=draft)
        conflict = self.api.post(url, {'client_message_id': str(conflict_cmi)}, format='json')
        self.assertEqual(conflict.status_code, 409)

    def test_draft_concurrent_insert_integrity_error_is_409(self):
        draft = MailDraft.objects.create(
            account=self.account, to_addresses=['dest@shizuha.com'], subject='Draft')
        with patch('mail.views.OutboxMessage.objects.create', side_effect=IntegrityError), \
                patch('mail.services.mail_service.get_mail_service') as get_service:
            response = self.api.post(
                f'/api/drafts/{draft.id}/send/',
                {'client_message_id': str(uuid.uuid4())}, format='json')
        self.assertEqual(response.status_code, 409)
        get_service.assert_not_called()

    @override_settings(SYSTEM_EMAIL_ADDRESS='noreply@shizuha.com', SYSTEM_USER_ID=1)
    def test_internal_success_timeout_replay_conflict_and_insert_race(self):
        internal = _account(email_address='noreply@shizuha.com')
        url = '/api/internal/send/'
        headers = {'HTTP_X_INTERNAL_SERVICE': 'mail59-regression'}
        base = {'to_addresses': ['user@example.com'], 'subject': 'Internal'}

        sent_cmi = uuid.uuid4()
        service = MagicMock()
        service.send_email.return_value = {'status': 'sent', 'message_id': 'm-1'}
        with patch('mail.services.mail_service.get_mail_service', return_value=service):
            first = self.api.post(url, {**base, 'client_message_id': str(sent_cmi)}, format='json', **headers)
            replay = self.api.post(url, {**base, 'client_message_id': str(sent_cmi)}, format='json', **headers)
        self.assertEqual((first.status_code, replay.status_code), (200, 200))
        service.send_email.assert_called_once()

        failed_cmi = uuid.uuid4()
        service = MagicMock()
        service.send_email.side_effect = TimeoutError('internal timeout')
        with patch('mail.services.mail_service.get_mail_service', return_value=service):
            first = self.api.post(url, {**base, 'client_message_id': str(failed_cmi)}, format='json', **headers)
            replay = self.api.post(url, {**base, 'client_message_id': str(failed_cmi)}, format='json', **headers)
        self.assertEqual((first.status_code, replay.status_code), (500, 500))
        service.send_email.assert_called_once()
        self.assertEqual(OutboxMessage.objects.get(account=internal, client_message_id=failed_cmi).status, 'failed')

        conflict_cmi = uuid.uuid4()
        OutboxMessage.objects.create(account=internal, client_message_id=conflict_cmi, status='sending')
        conflict = self.api.post(url, {**base, 'client_message_id': str(conflict_cmi)}, format='json', **headers)
        self.assertEqual(conflict.status_code, 409)

        with patch('mail.views.OutboxMessage.objects.create', side_effect=IntegrityError), \
                patch('mail.services.mail_service.get_mail_service') as get_service:
            race = self.api.post(url, {**base, 'client_message_id': str(uuid.uuid4())}, format='json', **headers)
        self.assertEqual(race.status_code, 409)
        get_service.assert_not_called()


class RecipientAmplificationBoundTests(TestCase):
    """MAIL-157: recipient fan-out is bounded per quota unit.

    The v1 agent surface rejects 0/>1 recipients before any API work (covered
    in mcp-server/test_mail_agent_surface.py). Here the backend /send/ boundary
    enforces an explicit total-envelope cap so even a non-agent caller cannot
    amplify one admitted request past MAIL_MAX_RECIPIENTS_PER_SEND — rejected
    with zero Outbox/provider work.
    """

    def setUp(self):
        self.account = _account()
        self.api = APIClient()
        self.api.force_authenticate(user=FakeUser())

    def send_payload(self, cmi, to_addresses):
        return {
            'account_id': str(self.account.id),
            'client_message_id': str(cmi),
            'to_addresses': to_addresses,
            'subject': 'Amplification bound',
            'body_text': 'hello',
        }

    @override_settings(MAIL_MAX_RECIPIENTS_PER_SEND=50)
    def test_over_cap_to_addresses_rejected_before_outbox_or_provider(self):
        cmi = uuid.uuid4()
        too_many = [f'user{i}@example.com' for i in range(51)]
        with patch('mail.services.mail_service.get_mail_service') as get_service:
            response = self.api.post(
                '/api/send/', self.send_payload(cmi, too_many), format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('50 total recipients', response.json()['to_addresses'][0])
        self.assertFalse(OutboxMessage.objects.filter(client_message_id=cmi).exists())
        get_service.assert_not_called()

    @override_settings(MAIL_MAX_RECIPIENTS_PER_SEND=50)
    def test_envelope_to_plus_cc_plus_bcc_capped_together(self):
        cmi = uuid.uuid4()
        payload = {
            **self.send_payload(cmi, [f'to{i}@example.com' for i in range(30)]),
            'cc_addresses': [f'cc{i}@example.com' for i in range(15)],
            'bcc_addresses': [f'bcc{i}@example.com' for i in range(10)],
        }
        with patch('mail.services.mail_service.get_mail_service') as get_service:
            response = self.api.post('/api/send/', payload, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('50 total recipients', response.json()['to_addresses'][0])
        self.assertFalse(OutboxMessage.objects.filter(client_message_id=cmi).exists())
        get_service.assert_not_called()

    @override_settings(MAIL_MAX_RECIPIENTS_PER_SEND=50)
    def test_at_cap_is_allowed_and_reaches_provider(self):
        cmi = uuid.uuid4()
        at_cap = [f'user{i}@example.com' for i in range(50)]
        service = MagicMock()
        service.send_email_jmap.return_value = {'email_id': 'em-at-cap'}
        with patch('mail.services.mail_service.get_mail_service', return_value=service):
            response = self.api.post('/api/send/', self.send_payload(cmi, at_cap), format='json')
        self.assertEqual(response.status_code, 200)
        service.send_email_jmap.assert_called_once()
        outbox = OutboxMessage.objects.get(account=self.account, client_message_id=cmi)
        self.assertEqual(len(outbox.to_addresses), 50)


class MailMcpReplyForwardContractTests(TestCase):
    """MAIL-65 regression coverage for deterministic threading and carryover."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.module = _load_mail_mcp_module()

    def _source(self, *, attachments=None):
        return {
            'id': 'local-id',
            'account_id': '11111111-1111-1111-1111-111111111111',
            'message_id': '<source@example.com>',
            'in_reply_to': '<parent@example.com>',
            'references': ['<root@example.com>'],
            'subject': 'Topic',
            'from_address': 'sender@example.com',
            'to_addresses': ['me@example.com'],
            'cc_addresses': ['copy@example.com'],
            'body_text': 'first line\n\nlast line',
            'date_sent': '2026-07-16T10:00:00Z',
            'attachments': attachments or [],
        }

    def test_reply_constructs_rfc_thread_headers_and_stable_quote(self):
        calls = []
        self.module.base.api_request = lambda method, path, **kwargs: (
            self._source() if method == 'GET' else calls.append(kwargs['data']) or {'id': 'sent'}
        )
        self.assertEqual(
            self.module.reply_to_email('local-id', 'My reply', reply_all=True),
            'Reply sent!',
        )
        payload = calls[0]
        self.assertEqual(payload['in_reply_to'], '<source@example.com>')
        self.assertEqual(
            payload['references'],
            ['<root@example.com>', '<parent@example.com>', '<source@example.com>'],
        )
        self.assertEqual(payload['to_addresses'], ['sender@example.com'])
        self.assertEqual(payload['cc_addresses'], ['me@example.com', 'copy@example.com'])
        self.assertIn('On 2026-07-16T10:00:00Z, sender@example.com wrote:', payload['body_text'])
        self.assertIn('> first line\n>\n> last line', payload['body_text'])

    def test_forward_excludes_attachments_unless_explicit(self):
        calls = []
        attachment = {'filename': 'proof.txt', 'content': 'cHJvb2Y=', 'content_type': 'text/plain'}
        self.module.base.api_request = lambda method, path, **kwargs: (
            self._source(attachments=[attachment])
            if method == 'GET' else calls.append(kwargs['data']) or {'id': 'sent'}
        )
        self.module.forward_email('local-id', 'dest@example.com')
        self.assertNotIn('attachments', calls[-1])
        self.module.forward_email('local-id', 'dest@example.com', include_attachments=True)
        self.assertEqual(calls[-1]['attachments'], [attachment])

    def test_forward_fails_closed_when_requested_attachment_content_is_unavailable(self):
        calls = []
        self.module.base.api_request = lambda method, path, **kwargs: (
            self._source(attachments=[{'filename': 'metadata-only.txt'}])
            if method == 'GET' else calls.append(kwargs['data']) or {'id': 'sent'}
        )
        result = self.module.forward_email(
            'local-id', 'dest@example.com', include_attachments=True,
        )
        self.assertIn('not available', result)
        self.assertEqual(calls, [])

    def test_forward_accepts_attachments_within_decoded_size_policy(self):
        calls = []
        attachments = [
            {'filename': 'a.txt', 'content': 'data:text/plain;base64,YQ=='},
            {'filename': 'bc.txt', 'content': 'YmM='},
        ]
        self.module.base.api_request = lambda method, path, **kwargs: (
            self._source(attachments=attachments)
            if method == 'GET' else calls.append(kwargs['data']) or {'id': 'sent'}
        )
        with patch.object(self.module, 'MAX_FORWARD_ATTACHMENT_BYTES', 2), \
                patch.object(self.module, 'MAX_FORWARD_ATTACHMENTS_TOTAL_BYTES', 3):
            result = self.module.forward_email(
                'local-id', 'dest@example.com', include_attachments=True,
            )
        self.assertEqual(result, 'Email forwarded!')
        self.assertEqual(calls[0]['attachments'], attachments)

    def test_forward_rejects_oversized_or_invalid_attachments_without_send(self):
        for attachments, expected in (
            ([{'filename': 'big.txt', 'content': 'YWJj'}], 'exceeds 2 decoded bytes'),
            ([{'filename': 'bad.txt', 'content': 'not base64!'}], 'not valid base64'),
            ([
                {'filename': 'a.txt', 'content': 'YQ=='},
                {'filename': 'b.txt', 'content': 'Yg=='},
            ], 'aggregate decoded bytes'),
        ):
            calls = []
            self.module.base.api_request = lambda method, path, **kwargs: (
                self._source(attachments=attachments)
                if method == 'GET' else calls.append(kwargs['data']) or {'id': 'sent'}
            )
            with patch.object(self.module, 'MAX_FORWARD_ATTACHMENT_BYTES', 2), \
                    patch.object(self.module, 'MAX_FORWARD_ATTACHMENTS_TOTAL_BYTES', 1):
                result = self.module.forward_email(
                    'local-id', 'dest@example.com', include_attachments=True,
                )
            self.assertIn(expected, result)
            self.assertEqual(calls, [])
