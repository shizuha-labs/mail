"""MAIL-164: Mail send API silently drops `body`/`html` fields.

Regression tests:
- Sending with `body` persists it as body_text (no more 200 "sent" with an
  empty-body message).
- Sending with `html` persists it as body_html.
- Canonical `body_text`/`body_html` still win when both are provided.
- The send endpoint's OpenAPI schema documents a request body (drf_spectacular
  can introspect MailSendViewSet now that it exposes serializer_class).
"""
import uuid
from unittest.mock import MagicMock, patch

from django.test import TestCase
from rest_framework.test import APIClient

from mail.models import MailAccount, OutboxMessage
from mail.tests.unit.test_mail59_send_idempotency import FakeUser


def _account():
    # user_id must match FakeUser.id (1) so get_user_accounts() resolves the
    # account for the force_authenticated FakeUser.
    return MailAccount.objects.create(
        user_id=1,
        email_address='mail164@shizuha.local',
        incoming_server_type='jmap',
        incoming_host='mail.shizuha.com',
        incoming_port=8543,
        incoming_security='ssl',
        incoming_username='mail164',
        outgoing_host='mail.shizuha.com',
        outgoing_port=587,
        outgoing_security='starttls',
        outgoing_username='mail164',
    )


class MailSendBodyHtmlAliasTests(TestCase):
    """Exercise the real /api/send/ endpoint with body/html aliases."""

    def setUp(self):
        self.account = _account()
        self.api = APIClient()
        self.api.force_authenticate(user=FakeUser())

    def _post(self, payload):
        service = MagicMock()
        service.send_email_jmap.return_value = {'email_id': 'em-mail164'}
        with patch('mail.services.mail_service.get_mail_service', return_value=service):
            return self.api.post('/api/send/', payload, format='json')

    def test_body_alias_is_persisted(self):
        """`body` must be persisted as body_text, not silently dropped."""
        cmi = uuid.uuid4()
        response = self._post({
            'account_id': str(self.account.id),
            'client_message_id': str(cmi),
            'to_addresses': ['dest@shizuha.com'],
            'subject': 'MAIL-164 body alias',
            'body': 'MAIL-164 round-trip probe body content',
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'sent')
        outbox = OutboxMessage.objects.get(
            account=self.account, client_message_id=cmi,
        )
        self.assertEqual(outbox.body_text, 'MAIL-164 round-trip probe body content')
        self.assertEqual(outbox.body_html, '')

    def test_html_alias_is_persisted(self):
        """`html` must be persisted as body_html, not silently dropped."""
        cmi = uuid.uuid4()
        response = self._post({
            'account_id': str(self.account.id),
            'client_message_id': str(cmi),
            'to_addresses': ['dest@shizuha.com'],
            'subject': 'MAIL-164 html alias',
            'html': '<p>HTML-MARKER-164</p>',
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'sent')
        outbox = OutboxMessage.objects.get(
            account=self.account, client_message_id=cmi,
        )
        self.assertEqual(outbox.body_html, '<p>HTML-MARKER-164</p>')
        self.assertEqual(outbox.body_text, '')

    def test_canonical_body_text_wins_over_alias(self):
        """Explicit body_text takes precedence over the body alias."""
        cmi = uuid.uuid4()
        response = self._post({
            'account_id': str(self.account.id),
            'client_message_id': str(cmi),
            'to_addresses': ['dest@shizuha.com'],
            'subject': 'MAIL-164 precedence',
            'body': 'alias body',
            'body_text': 'canonical body',
        })
        self.assertEqual(response.status_code, 200)
        outbox = OutboxMessage.objects.get(
            account=self.account, client_message_id=cmi,
        )
        self.assertEqual(outbox.body_text, 'canonical body')

    def test_canonical_body_html_wins_over_alias(self):
        """Explicit body_html takes precedence over the html alias."""
        cmi = uuid.uuid4()
        response = self._post({
            'account_id': str(self.account.id),
            'client_message_id': str(cmi),
            'to_addresses': ['dest@shizuha.com'],
            'subject': 'MAIL-164 precedence html',
            'html': 'alias html',
            'body_html': '<b>canonical html</b>',
        })
        self.assertEqual(response.status_code, 200)
        outbox = OutboxMessage.objects.get(
            account=self.account, client_message_id=cmi,
        )
        self.assertEqual(outbox.body_html, '<b>canonical html</b>')


class MailSendSchemaTests(TestCase):
    """The send endpoint's OpenAPI schema must document a request body."""

    def test_send_endpoint_schema_documents_request_body(self):
        from drf_spectacular.validation import validate_schema
        from drf_spectacular.generators import SchemaGenerator

        generator = SchemaGenerator(patterns=None)
        schema = generator.get_schema(request=None, public=True)
        # validate_schema raises on structural errors; also assert the send
        # path exists and carries a requestBody.
        validate_schema(schema)

        send_path = None
        for path, methods in schema.get('paths', {}).items():
            if path.endswith('/api/send/'):
                send_path = (path, methods)
                break
        self.assertIsNotNone(send_path, 'POST /api/send/ missing from schema')
        path, methods = send_path
        self.assertIn('post', methods)
        request_body = methods['post'].get('requestBody')
        self.assertIsNotNone(
            request_body,
            'POST /api/send/ must document a requestBody (MAIL-164)',
        )
        content = request_body.get('content', {})
        self.assertIn('application/json', content)
        schema_ref = content['application/json'].get('schema', {})
        self.assertTrue(
            schema_ref,
            'requestBody must reference a schema (MailSendSerializer)',
        )
