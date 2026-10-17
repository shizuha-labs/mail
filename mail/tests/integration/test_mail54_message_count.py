from mail.tests.active_identity import ActiveIdentityTestCase
from unittest.mock import patch

from django.test import TestCase, SimpleTestCase, override_settings
from rest_framework.test import APIClient

from mail.models import MailAccount
from mail.services.mail_service import MailService
from mail.tests.helpers import create_test_jwt


class _FolderMessageService:
    def sync_folders(self, account):
        return [
            {'role': 'inbox', 'jmap_mailbox_id': 'inbox-id', 'name': 'Inbox'},
            {'role': 'sent', 'jmap_mailbox_id': 'sent-id', 'name': 'Sent'},
            {'role': 'trash', 'jmap_mailbox_id': 'trash-id', 'name': 'Trash'},
        ]

    def sync_messages(self, account, folder_id):
        if folder_id == 'trash-id':
            return {'messages': [], 'total': 0}
        return {
            'messages': [{
                'jmap_email_id': f'{folder_id}-msg',
                'subject': f'{folder_id} subject',
                'from_address': 'sender@example.com',
                'to_addresses': [{'email': account.email_address}],
                'preview': f'{folder_id} body',
            }],
            # Reproduces MAIL-54: Stalwart/JMAP can return rows while the
            # service-level total is absent/zero; the API must not report an
            # empty folder when it has rows in this page.
            'total': 0,
        }


class MessageListCountTests(ActiveIdentityTestCase):
    def setUp(self):
        self.user_id = 54054
        self.account = MailAccount.objects.create(
            user_id=self.user_id,
            email_address='mail54@shizuha.local',
            incoming_server_type='jmap',
            incoming_host='stalwart.local',
            incoming_port=8080,
            incoming_username='mail54@shizuha.local',
            outgoing_host='stalwart.local',
        )
        self.client = APIClient()
        token = create_test_jwt(user_id=self.user_id, email=self.account.email_address)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')

    def test_folder_role_list_count_is_at_least_returned_rows(self):
        with patch('mail.services.mail_service.get_mail_service', return_value=_FolderMessageService()):
            for role in ('inbox', 'sent'):
                with self.subTest(role=role):
                    response = self.client.get(f'/api/messages/?folder_role={role}')

                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(len(response.data['results']), 1)
                    self.assertEqual(response.data['count'], 1)

    def test_empty_folder_still_reports_zero_count(self):
        with patch('mail.services.mail_service.get_mail_service', return_value=_FolderMessageService()):
            response = self.client.get('/api/messages/?folder_role=trash')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 0)
        self.assertEqual(response.data['results'], [])


class _FakeResponse:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body

    def json(self):
        return self._body


class _FakeHttpxClient:
    posted_json = None

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def get(self, *args, **kwargs):
        return _FakeResponse(200, {
            'primaryAccounts': {'urn:ietf:params:jmap:mail': 'acct-1'},
        })

    def post(self, *args, **kwargs):
        type(self).posted_json = kwargs['json']
        return _FakeResponse(200, {
            'methodResponses': [
                ['Email/query', {
                    'accountId': 'acct-1',
                    'ids': ['email-1'],
                }, 'query'],
                ['Email/get', {
                    'accountId': 'acct-1',
                    'list': [{
                        'id': 'email-1',
                        'threadId': 'thread-1',
                        'subject': 'Missing total fixture',
                        'from': [{'email': 'sender@example.com'}],
                        'to': [{'email': 'mail54@shizuha.local'}],
                        'keywords': {},
                    }],
                }, 'emails'],
            ],
        })


@override_settings(STALWART_VERIFY_SSL=False)
class JmapMessageCountTests(TestCase):
    def test_jmap_query_requests_total_and_falls_back_to_returned_rows(self):
        account = MailAccount.objects.create(
            user_id=54055,
            email_address='mail54-jmap@shizuha.local',
            incoming_server_type='jmap',
            incoming_host='stalwart.local',
            incoming_port=8080,
            incoming_username='mail54-jmap@shizuha.local',
            outgoing_host='stalwart.local',
        )

        with patch('mail.services.mail_service.httpx.Client', _FakeHttpxClient):
            result = MailService()._sync_messages_jmap(account, 'inbox-id', 50)

        query_call = _FakeHttpxClient.posted_json['methodCalls'][0]
        self.assertEqual(query_call[0], 'Email/query')
        self.assertIs(query_call[1]['calculateTotal'], True)
        self.assertEqual(len(result['messages']), 1)
        self.assertEqual(result['total'], 1)
