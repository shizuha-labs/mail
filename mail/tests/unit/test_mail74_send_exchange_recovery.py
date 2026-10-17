"""MAIL-74 production-order: exchange fail must not poison client_message_id.

Sequence (public + draft send):
  1) managed_identity send, exchange fails → 503, zero provider calls, no terminal poison
  2) same client_message_id, exchange succeeds → 200, provider total == 1
"""
import uuid
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from mail.models import MailAccount, MailDraft, OutboxMessage
from mail.services.delegated_token import DelegatedTokenError

User = get_user_model()


class _ProviderResponse:
    def __init__(self, data):
        self.status_code = 200
        self._data = data

    def json(self):
        return self._data


class _ProviderTransport:
    """Provider-only fake; MailService auth/token resolution remains real."""

    def __init__(self):
        self.calls = []
        self.deliveries = 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def get(self, url, *, auth):
        self.calls.append(('get', url, auth.token))
        return _ProviderResponse({
            'primaryAccounts': {
                'urn:ietf:params:jmap:mail': 'mail-account',
                'urn:ietf:params:jmap:submission': 'submission-account',
            },
        })

    def post(self, url, *, auth, headers, json):
        methods = [call[0] for call in json['methodCalls']]
        self.calls.append(('post', tuple(methods), auth.token))
        if methods == ['Mailbox/get']:
            return _ProviderResponse({
                'methodResponses': [[
                    'Mailbox/get',
                    {'list': [
                        {'id': 'sent', 'role': 'sent'},
                        {'id': 'drafts', 'role': 'drafts'},
                    ]},
                    'mailboxes',
                ]],
            })
        if methods == ['Identity/get']:
            return _ProviderResponse({
                'methodResponses': [[
                    'Identity/get',
                    {'list': [{'id': 'identity', 'email': 'managed@shizuha.com'}]},
                    'identities',
                ]],
            })
        self.deliveries += 1
        return _ProviderResponse({
            'methodResponses': [
                ['Email/set', {'created': {'draft': {'id': 'em-recovered'}}}, 'email'],
                ['EmailSubmission/set', {'created': {'submission': {'id': 'sub'}}}, 'submit'],
            ],
        })


def _managed_account(**overrides):
    defaults = dict(
        user_id=1,
        email_address='managed@shizuha.com',
        incoming_server_type='jmap',
        incoming_host='stalwart.example.com',
        incoming_port=8080,
        incoming_username='managed@shizuha.com',
        outgoing_host='stalwart.example.com',
        is_active=True,
        settings={'managed_identity': True},
    )
    defaults.update(overrides)
    return MailAccount.objects.create(**defaults)


class Mail74SendExchangeRecoveryTests(TestCase):
    def setUp(self):
        self.api = APIClient()
        self.user = User.objects.create_user(
            username='mail74-send', email='managed@shizuha.com', password='x',
        )
        self.account = _managed_account(user_id=self.user.pk)
        self.cmi = str(uuid.uuid4())
        self.api.force_authenticate(user=self.user)
        self.api.credentials(HTTP_AUTHORIZATION='Bearer subject-access-token')

    def _send_payload(self):
        return {
            'account_id': self.account.id,
            'to_addresses': ['dest@shizuha.com'],
            'subject': 'MAIL-74 recovery',
            'body_text': 'hello',
            'client_message_id': self.cmi,
        }

    def test_public_send_exchange_fail_then_recover(self):
        provider = _ProviderTransport()
        with patch(
            'mail.services.delegated_token.resolve_provider_bearer',
            side_effect=[
                DelegatedTokenError('no entitlement'),
                'delegated-bearer-ok',
            ],
        ) as exchange, patch(
            'mail.services.mail_service.httpx.Client', return_value=provider,
        ):
            r1 = self.api.post('/api/send/', self._send_payload(), format='json')
            self.assertEqual(r1.status_code, 503, r1.content)
            self.assertEqual(exchange.call_count, 1)
            self.assertEqual(provider.calls, [])
            self.assertFalse(
                OutboxMessage.objects.filter(
                    account=self.account, client_message_id=self.cmi, status='failed',
                ).exists()
            )
            r2 = self.api.post('/api/send/', self._send_payload(), format='json')
            self.assertEqual(r2.status_code, 200, r2.content)
            self.assertEqual(exchange.call_count, 2)
            self.assertEqual(provider.deliveries, 1)
            self.assertTrue(provider.calls)
            self.assertEqual({call[-1] for call in provider.calls}, {'delegated-bearer-ok'})
            self.assertEqual(r2.json().get('email_id'), 'em-recovered')

    def test_public_send_typed_preprovider_failure_recovers(self):
        """Only our typed provider_started=False boundary permits re-attempt."""
        OutboxMessage.objects.create(
            account=self.account,
            client_message_id=uuid.UUID(self.cmi),
            status='failed',
            provider_started=False,
            to_addresses=['dest@shizuha.com'],
            subject='MAIL-74 recovery',
            error='arbitrary pre-provider failure',
        )
        provider = _ProviderTransport()
        with patch(
            'mail.services.delegated_token.resolve_provider_bearer',
            return_value='delegated-bearer-ok',
        ) as exchange, patch(
            'mail.services.mail_service.httpx.Client', return_value=provider,
        ):
            r = self.api.post('/api/send/', self._send_payload(), format='json')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(exchange.call_count, 1)
        self.assertEqual(provider.deliveries, 1)
        outbox = OutboxMessage.objects.get(account=self.account, client_message_id=self.cmi)
        self.assertEqual(outbox.status, 'sent')
        self.assertIs(outbox.provider_started, True)

    def test_provider_started_failure_never_rearms_from_error_text(self):
        OutboxMessage.objects.create(
            account=self.account,
            client_message_id=uuid.UUID(self.cmi),
            status='failed',
            provider_started=True,
            to_addresses=['dest@shizuha.com'],
            subject='MAIL-74 recovery',
            error='retryable:delegated_token:provider echoed delegated token',
        )
        with patch('mail.services.delegated_token.resolve_provider_bearer') as exchange:
            r = self.api.post('/api/send/', self._send_payload(), format='json')
        self.assertEqual(r.status_code, 500, r.content)
        exchange.assert_not_called()

    def test_draft_send_exchange_fail_then_recover(self):
        draft = MailDraft.objects.create(
            account=self.account,
            to_addresses=['dest@shizuha.com'],
            subject='draft recovery',
            body_text='d',
        )
        cmi = str(uuid.uuid4())
        provider = _ProviderTransport()
        with patch(
            'mail.services.delegated_token.resolve_provider_bearer',
            side_effect=[
                DelegatedTokenError('exchange down'),
                'delegated-bearer-ok',
            ],
        ) as exchange, patch(
            'mail.services.mail_service.httpx.Client', return_value=provider,
        ):
            r1 = self.api.post(
                f'/api/drafts/{draft.id}/send/',
                {'client_message_id': cmi},
                format='json',
            )
            self.assertEqual(r1.status_code, 503, r1.content)
            self.assertEqual(exchange.call_count, 1)
            self.assertEqual(provider.calls, [])
            r2 = self.api.post(
                f'/api/drafts/{draft.id}/send/',
                {'client_message_id': cmi},
                format='json',
            )
            self.assertEqual(r2.status_code, 200, r2.content)
            self.assertEqual(exchange.call_count, 2)
            self.assertEqual(provider.deliveries, 1)
            self.assertEqual({call[-1] for call in provider.calls}, {'delegated-bearer-ok'})
