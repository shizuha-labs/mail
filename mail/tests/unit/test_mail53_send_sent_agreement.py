"""MAIL-53: JMAP send — the API response and the Sent folder must agree.

Regression for the 500-despite-message-in-Sent bug: the old flow created the
message directly in the Sent mailbox via Email/set BEFORE the submission ran,
so any EmailSubmission/set failure raised MailServiceError (-> HTTP 500) while
a copy already sat in Sent. The fixed flow creates the message as a draft in
the Drafts mailbox and moves it to Sent only via onSuccessUpdateEmail, which
the server applies IFF the submission is created.
"""
from unittest.mock import MagicMock, Mock, patch

from django.test import TestCase

from mail.models import MailAccount
from mail.services.mail_service import MailService, MailServiceError, _on_success_update

SENT_ID = 'mb-sent'
DRAFTS_ID = 'mb-drafts'
ACCOUNT_ID = 'acc-1'


def _account(**overrides):
    defaults = dict(
        user_id=1,
        email_address='m53@shizuha.com',
        incoming_server_type='jmap',
        incoming_host='stalwart.example.com',
        incoming_port=8080,
        incoming_username='m53@shizuha.com',
        outgoing_host='stalwart.example.com',
        is_active=True,
    )
    defaults.update(overrides)
    return MailAccount.objects.create(**defaults)


def _response(status_code=200, payload=None):
    resp = Mock()
    resp.status_code = status_code
    resp.json.return_value = payload or {}
    return resp


def _session_payload():
    return {
        'primaryAccounts': {
            'urn:ietf:params:jmap:mail': ACCOUNT_ID,
            'urn:ietf:params:jmap:submission': ACCOUNT_ID,
        }
    }


def _mailbox_payload():
    return {
        'methodResponses': [
            ['Mailbox/get', {'list': [
                {'id': DRAFTS_ID, 'role': 'drafts'},
                {'id': SENT_ID, 'role': 'sent'},
            ]}, 'mb'],
        ]
    }


def _identity_payload(identities=None):
    if identities is None:
        identities = [{'id': 'ident-1', 'email': 'm53@shizuha.com'}]
    return {'methodResponses': [['Identity/get', {'list': identities}, 'identities']]}


class JmapSendSentAgreementTests(TestCase):
    """Drive send_email_jmap against a mocked httpx.Client."""

    def setUp(self):
        self.account = _account()
        self.service = MailService()

    def _run(self, send_payload, identity_payload=None):
        with patch('mail.services.mail_service.httpx.Client') as client_cls:
            client = MagicMock()
            client_cls.return_value.__enter__.return_value = client
            client.get.return_value = _response(200, _session_payload())
            client.post.side_effect = [
                _response(200, _mailbox_payload()),
                _response(200, identity_payload or _identity_payload()),
                _response(200, send_payload),
            ]
            result = self.service.send_email_jmap(
                self.account, ['dest@shizuha.com'], 'subj', body_text='hello'
            )
            return result, client

    def test_success_creates_draft_and_moves_to_sent_on_submission(self):
        send_payload = {
            'methodResponses': [
                ['Email/set', {'created': {'draft': {'id': 'em-1'}}}, 'email'],
                ['EmailSubmission/set', {'created': {'submission': {'id': 'sub-1'}}}, 'submit'],
            ]
        }
        result, client = self._run(send_payload)

        self.assertEqual(result['status'], 'sent')
        self.assertEqual(result['email_id'], 'em-1')

        send_body = client.post.call_args_list[2].kwargs['json']
        email_set = send_body['methodCalls'][0][1]
        submission_set = send_body['methodCalls'][1][1]
        email_create = email_set['create']['draft']

        # Created as a DRAFT in Drafts — never directly into Sent.
        self.assertEqual(email_create['mailboxIds'], {DRAFTS_ID: True})
        self.assertEqual(email_create['keywords'], {'$draft': True})

        # Moves to Sent only when the submission is actually created.
        on_success = submission_set['onSuccessUpdateEmail']['#submission']
        self.assertEqual(on_success[f'mailboxIds/{SENT_ID}'], True)
        self.assertIsNone(on_success[f'mailboxIds/{DRAFTS_ID}'])
        self.assertIsNone(on_success['keywords/$draft'])

        # identityId is mandatory (RFC 8621 §7.5) and must be present.
        self.assertEqual(submission_set['create']['submission']['identityId'], 'ident-1')

    def test_submission_failure_raises_specific_error_not_phantom_sent(self):
        send_payload = {
            'methodResponses': [
                ['Email/set', {'created': {'draft': {'id': 'em-1'}}}, 'email'],
                ['EmailSubmission/set', {
                    'notCreated': {'submission': {'type': 'invalidProperties',
                                                  'properties': ['identityId']}},
                }, 'submit'],
            ]
        }
        with self.assertRaises(MailServiceError) as ctx:
            self._run(send_payload)

        msg = str(ctx.exception)
        self.assertIn('left in Drafts', msg)
        self.assertIn('EmailSubmission/set', msg)

    def test_missing_identity_fails_early_before_any_email_is_created(self):
        with self.assertRaises(MailServiceError) as ctx:
            self._run(
                send_payload={'methodResponses': []},
                identity_payload=_identity_payload(identities=[]),
            )
        self.assertIn('No JMAP identity', str(ctx.exception))

    def test_on_success_update_shape(self):
        self.assertEqual(
            _on_success_update(SENT_ID, DRAFTS_ID),
            {'keywords/$draft': None, f'mailboxIds/{SENT_ID}': True, f'mailboxIds/{DRAFTS_ID}': None},
        )
        self.assertEqual(_on_success_update(None, None), {'keywords/$draft': None})
