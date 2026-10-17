"""
Unit tests for JMAP client.
"""
from unittest.mock import Mock, patch, MagicMock
from django.test import TestCase

from mail.services.jmap_client import JMAPClient


class JMAPClientTestCase(TestCase):
    """Tests for JMAP client."""

    def setUp(self):
        self.client = JMAPClient(
            base_url='http://stalwart:8080',
            auth_token='test-token'
        )
        # Set account_id to avoid session fetch
        self.client._account_id = 'account-123'

    @patch('httpx.Client')
    def test_get_session_sync(self, mock_client_class):
        """Test session discovery."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__.return_value = mock_client

        mock_response = Mock()
        mock_response.json.return_value = {
            'apiUrl': 'http://stalwart:8080/jmap/',
            'downloadUrl': 'http://stalwart:8080/jmap/download/{accountId}/{blobId}/{name}',
            'uploadUrl': 'http://stalwart:8080/jmap/upload/{accountId}/',
            'accounts': {
                'account-123': {
                    'name': 'Test Account',
                    'accountCapabilities': {}
                }
            },
            'primaryAccounts': {
                'urn:ietf:params:jmap:mail': 'account-123'
            }
        }
        mock_client.get.return_value = mock_response

        # Clear cached session
        self.client._session = None
        self.client._account_id = None

        session = self.client.get_session_sync()

        self.assertEqual(session['apiUrl'], 'http://stalwart:8080/jmap/')
        self.assertIn('account-123', session['accounts'])
        self.assertEqual(self.client._account_id, 'account-123')

    @patch('httpx.Client')
    def test_mailbox_get(self, mock_client_class):
        """Test getting mailboxes."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__.return_value = mock_client

        mock_response = Mock()
        mock_response.json.return_value = {
            'methodResponses': [
                ['Mailbox/get', {
                    'accountId': 'account-123',
                    'list': [
                        {
                            'id': 'inbox-id',
                            'name': 'Inbox',
                            'role': 'inbox',
                            'totalEmails': 10,
                            'unreadEmails': 3
                        },
                        {
                            'id': 'sent-id',
                            'name': 'Sent',
                            'role': 'sent',
                            'totalEmails': 5,
                            'unreadEmails': 0
                        }
                    ]
                }, 'mailbox-get']
            ]
        }
        mock_client.post.return_value = mock_response

        result = self.client.mailbox_get()

        self.assertEqual(len(result['list']), 2)
        self.assertEqual(result['list'][0]['name'], 'Inbox')

    @patch('httpx.Client')
    def test_email_query(self, mock_client_class):
        """Test querying emails."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__.return_value = mock_client

        mock_response = Mock()
        mock_response.json.return_value = {
            'methodResponses': [
                ['Email/query', {
                    'accountId': 'account-123',
                    'ids': ['email-1', 'email-2'],
                    'position': 0,
                    'total': 2
                }, 'email-query']
            ]
        }
        mock_client.post.return_value = mock_response

        result = self.client.email_query(
            filter_obj={'inMailbox': 'inbox-id'},
            limit=50
        )

        self.assertEqual(len(result['ids']), 2)
        self.assertIn('email-1', result['ids'])

    @patch('httpx.Client')
    def test_email_get(self, mock_client_class):
        """Test getting emails by ID."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__.return_value = mock_client

        mock_response = Mock()
        mock_response.json.return_value = {
            'methodResponses': [
                ['Email/get', {
                    'accountId': 'account-123',
                    'list': [
                        {
                            'id': 'email-1',
                            'subject': 'Test Email 1',
                            'from': [{'email': 'sender@example.com'}],
                            'preview': 'Preview text...'
                        },
                        {
                            'id': 'email-2',
                            'subject': 'Test Email 2',
                            'from': [{'email': 'other@example.com'}],
                            'preview': 'Another preview...'
                        }
                    ]
                }, 'email-get']
            ]
        }
        mock_client.post.return_value = mock_response

        result = self.client.email_get(ids=['email-1', 'email-2'])

        self.assertEqual(len(result['list']), 2)
        self.assertEqual(result['list'][0]['subject'], 'Test Email 1')

    @patch('httpx.Client')
    def test_email_query_and_get(self, mock_client_class):
        """Test combined query and get."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__.return_value = mock_client

        mock_response = Mock()
        mock_response.json.return_value = {
            'methodResponses': [
                ['Email/query', {
                    'accountId': 'account-123',
                    'ids': ['email-1', 'email-2']
                }, 'email-query'],
                ['Email/get', {
                    'accountId': 'account-123',
                    'list': [
                        {'id': 'email-1', 'subject': 'Test Email 1'},
                        {'id': 'email-2', 'subject': 'Test Email 2'}
                    ]
                }, 'email-get']
            ]
        }
        mock_client.post.return_value = mock_response

        result = self.client.email_query_and_get(
            filter_obj={'inMailbox': 'inbox-id'},
            limit=50
        )

        self.assertIn('query', result)
        self.assertIn('emails', result)
        self.assertEqual(len(result['emails']['list']), 2)

    @patch('httpx.Client')
    def test_email_set_create(self, mock_client_class):
        """Test creating an email."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__.return_value = mock_client

        mock_response = Mock()
        mock_response.json.return_value = {
            'methodResponses': [
                ['Email/set', {
                    'accountId': 'account-123',
                    'created': {
                        'draft': {'id': 'new-email-id'}
                    }
                }, 'email-set']
            ]
        }
        mock_client.post.return_value = mock_response

        result = self.client.email_set(
            create={
                'draft': {
                    'from': [{'email': 'sender@shizuha.com'}],
                    'to': [{'email': 'recipient@example.com'}],
                    'subject': 'Test Subject',
                    'textBody': [{'type': 'text/plain', 'value': 'Test body'}]
                }
            }
        )

        self.assertIn('created', result)
        self.assertIn('draft', result['created'])

    @patch('httpx.Client')
    def test_email_set_update(self, mock_client_class):
        """Test updating an email (e.g., marking as read)."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__.return_value = mock_client

        mock_response = Mock()
        mock_response.json.return_value = {
            'methodResponses': [
                ['Email/set', {
                    'accountId': 'account-123',
                    'updated': {'email-1': None}
                }, 'email-set']
            ]
        }
        mock_client.post.return_value = mock_response

        result = self.client.email_set(
            update={
                'email-1': {'keywords/$seen': True}
            }
        )

        self.assertIn('updated', result)
        self.assertIn('email-1', result['updated'])

    @patch('httpx.Client')
    def test_email_set_destroy(self, mock_client_class):
        """Test deleting an email."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__.return_value = mock_client

        mock_response = Mock()
        mock_response.json.return_value = {
            'methodResponses': [
                ['Email/set', {
                    'accountId': 'account-123',
                    'destroyed': ['email-1']
                }, 'email-set']
            ]
        }
        mock_client.post.return_value = mock_response

        result = self.client.email_set(destroy=['email-1'])

        self.assertIn('destroyed', result)
        self.assertIn('email-1', result['destroyed'])

    @patch('httpx.Client')
    def test_search_emails(self, mock_client_class):
        """Test searching emails."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__.return_value = mock_client

        mock_response = Mock()
        mock_response.json.return_value = {
            'methodResponses': [
                ['Email/query', {
                    'accountId': 'account-123',
                    'ids': ['email-match-1', 'email-match-2']
                }, 'email-query'],
                ['Email/get', {
                    'accountId': 'account-123',
                    'list': [
                        {'id': 'email-match-1', 'subject': 'Important Meeting'},
                        {'id': 'email-match-2', 'subject': 'Important Document'}
                    ]
                }, 'email-get']
            ]
        }
        mock_client.post.return_value = mock_response

        result = self.client.search_emails(query='important')

        self.assertEqual(len(result['emails']['list']), 2)

    @patch('httpx.Client')
    def test_email_submission_set(self, mock_client_class):
        """Test submitting email for sending."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__.return_value = mock_client

        mock_response = Mock()
        mock_response.json.return_value = {
            'methodResponses': [
                ['EmailSubmission/set', {
                    'accountId': 'account-123',
                    'created': {
                        'submission': {'id': 'submission-id'}
                    }
                }, 'submission-set']
            ]
        }
        mock_client.post.return_value = mock_response

        result = self.client.email_submission_set(
            create={
                'submission': {
                    'emailId': 'email-id',
                    'identityId': 'identity-123'
                }
            }
        )

        self.assertIn('created', result)
        self.assertIn('submission', result['created'])

    @patch('httpx.Client')
    def test_thread_get(self, mock_client_class):
        """Test getting threads."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__.return_value = mock_client

        mock_response = Mock()
        mock_response.json.return_value = {
            'methodResponses': [
                ['Thread/get', {
                    'accountId': 'account-123',
                    'list': [
                        {
                            'id': 'thread-1',
                            'emailIds': ['email-1', 'email-2', 'email-3']
                        }
                    ]
                }, 'thread-get']
            ]
        }
        mock_client.post.return_value = mock_response

        result = self.client.thread_get(ids=['thread-1'])

        self.assertEqual(len(result['list']), 1)
        self.assertEqual(len(result['list'][0]['emailIds']), 3)

    @patch('httpx.Client')
    def test_blob_download(self, mock_client_class):
        """Test downloading a blob."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__.return_value = mock_client

        mock_response = Mock()
        mock_response.content = b'attachment content here'
        mock_client.get.return_value = mock_response

        result = self.client.blob_download('blob-123')

        self.assertEqual(result, b'attachment content here')

    @patch('httpx.Client')
    def test_blob_upload(self, mock_client_class):
        """Test uploading a blob."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__.return_value = mock_client

        mock_response = Mock()
        mock_response.json.return_value = {
            'accountId': 'account-123',
            'blobId': 'new-blob-id',
            'size': 1234,
            'type': 'application/pdf'
        }
        mock_client.post.return_value = mock_response

        result = self.client.blob_upload(
            content=b'file content',
            content_type='application/pdf'
        )

        self.assertEqual(result['blobId'], 'new-blob-id')

    def test_build_request(self):
        """Test request building."""
        method_calls = [
            ['Email/query', {'accountId': 'account-123'}, 'query'],
            ['Email/get', {'#ids': {'resultOf': 'query'}}, 'get']
        ]

        request = self.client._build_request(method_calls)

        self.assertIn('using', request)
        self.assertIn('methodCalls', request)
        self.assertEqual(len(request['methodCalls']), 2)
        self.assertIn('urn:ietf:params:jmap:mail', request['using'])

    def test_get_method_response(self):
        """Test extracting method response by call ID."""
        response = {
            'methodResponses': [
                ['Email/query', {'ids': ['1', '2']}, 'query'],
                ['Email/get', {'list': []}, 'get']
            ]
        }

        query_result = self.client.get_method_response(response, 'query')
        get_result = self.client.get_method_response(response, 'get')
        missing = self.client.get_method_response(response, 'nonexistent')

        self.assertEqual(query_result['ids'], ['1', '2'])
        self.assertEqual(get_result['list'], [])
        self.assertIsNone(missing)

    def test_get_headers(self):
        """Test header generation."""
        headers = self.client._get_headers()

        self.assertEqual(headers['Content-Type'], 'application/json')
        self.assertEqual(headers['Authorization'], 'Bearer test-token')

    def test_get_headers_no_auth(self):
        """Test header generation without auth token."""
        client = JMAPClient(base_url='http://stalwart:8080')
        headers = client._get_headers()

        self.assertEqual(headers['Content-Type'], 'application/json')
        self.assertNotIn('Authorization', headers)
