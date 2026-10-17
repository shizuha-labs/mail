from mail.tests.active_identity import ActiveIdentityTestCase
"""
KOT-88: owner-scoped JMAP attachment blob proxy — unit/integration tests.

Covers the acceptance matrix for the mail backend leg:
- owner scoping: cross-owner and unknown accounts → non-enumerating 404;
- IMAP refusal: explicit 501, no provider request issued;
- happy path: downloadUrl template expanded with percent-escaped identifiers,
  provider auth preserved server-side, nosniff + forced attachment disposition,
  content-type passthrough;
- oversize refusal: provider-declared Content-Length over the cap → 413
  before any streaming;
- provider 404 → non-enumerating 404; provider auth failure → 502;
- unauthenticated → 401.

Run: DJANGO_SETTINGS_MODULE=mailmanager.ci_test_settings \
     python manage.py test mail.tests.integration.test_kot88_attachment_proxy
"""
from unittest.mock import MagicMock, patch

from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from mail.models import MailAccount
from mail.tests.helpers import create_test_jwt


DOWNLOAD_TEMPLATE = 'http://stalwart:8080/jmap/download/{accountId}/{blobId}/{name}'


def _jmap_account(user_id, email):
    return MailAccount.objects.create(
        user_id=user_id, email_address=email,
        incoming_host='stalwart-mail', outgoing_host='smtp.example.com',
        incoming_username=email, incoming_server_type='jmap',
        encrypted_incoming_password=b'enc:ci-fixture',
    )


def _imap_account(user_id, email):
    return MailAccount.objects.create(
        user_id=user_id, email_address=email,
        incoming_host='imap.example.com', outgoing_host='smtp.example.com',
        incoming_username=email, incoming_server_type='imap',
        encrypted_incoming_password=b'enc:ci-fixture',
    )


def _client_client(account):
    """Build the fake request-scoped Stalwart client the view undercuts."""
    fake = MagicMock()
    fake.base_url = 'http://stalwart:8080'
    fake.account_id = 'acc-111-222'
    fake.verify_ssl = True
    fake._get_headers.return_value = {'Authorization': 'Basic cnViejp1bnJlYWQ='}
    async def _session():
        return {'downloadUrl': DOWNLOAD_TEMPLATE, 'accounts': ['acc-111-222']}
    # The view wraps get_session in async_to_sync; provide a real coroutine.
    fake.get_session = lambda: _session()
    return fake


class KOT88AttachmentProxyTests(ActiveIdentityTestCase):
    OWNER_A, OWNER_B = 660001, 660002

    def setUp(self):
        self.client = APIClient()
        self.a_account = _jmap_account(self.OWNER_A, 'owner-a@shizuha.com')
        self.b_account = _jmap_account(self.OWNER_B, 'owner-b@shizuha.com')
        token = create_test_jwt(user_id=self.OWNER_A, email='owner-a@shizuha.com')
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
        self.url = '/api/attachments/blk-abc123/download/'


    def _cred(self):
        cred = MagicMock()
        cred.decrypt_password.return_value = 'unit-test-password'
        return patch('mail.services.credential_service.get_credential_service', return_value=cred)

    def test_unauthenticated_gets_401(self):
        anon = APIClient()
        response = anon.get(self.url, {'account_id': str(self.a_account.id)})
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_cross_owner_account_is_non_enumerating_404(self):
        response = self.client.get(self.url, {'account_id': str(self.b_account.id)})
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(response.json(), {'error': 'Mail account not found'})

    def test_unknown_account_is_non_enumerating_404(self):
        response = self.client.get(self.url, {'account_id': '00000000-0000-0000-0000-000000000000'})
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_imap_account_refused_501_without_provider_call(self):
        imap = _imap_account(self.OWNER_A, 'imap-owner@shizuha.com')
        fake_client = _client_client(imap)
        with patch('mail.tasks._get_stalwart_client', return_value=fake_client) as client_patch:
            response = self.client.get(self.url, {'account_id': str(imap.id)})
        self.assertEqual(response.status_code, status.HTTP_501_NOT_IMPLEMENTED)
        self.assertIn('JMAP', response.json()['error'])
        client_patch.assert_not_called()

    def test_happy_path_streams_with_escaped_ids_and_headers(self):
        fake_client = _client_client(self.a_account)
        probe = MagicMock()
        probe.status_code = 200
        probe.headers = {'content-length': '10', 'content-type': 'application/pdf'}

        def fake_stream(method, url, headers=None, timeout=None, verify=None, follow_redirects=None):
            self.assertEqual(method, 'GET')
            # Opaque ids percent-escaped; template shape preserved.
            self.assertEqual(
                url,
                'http://stalwart:8080/jmap/download/acc-111-222/blk-abc123/q%20report.pdf',
            )
            # Provider auth preserved server-side.
            self.assertEqual(headers['Authorization'], 'Basic cnViejp1bnJlYWQ=')
            body = MagicMock()
            body.raise_for_status = lambda: None
            body.iter_bytes = lambda chunk_size=None: iter([b'0123456789'])
            return MagicMock(__enter__=lambda s: body, __exit__=lambda *a: False)

        with patch('mail.tasks._get_stalwart_client', return_value=fake_client), \
             self._cred(), \
             patch('httpx.head', return_value=probe), \
             patch('httpx.stream', side_effect=fake_stream):
            response = self.client.get(
                self.url, {'account_id': str(self.a_account.id), 'filename': 'q report.pdf'},
            )
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            # Consume inside the patched context: streaming is lazy.
            payload = b''.join(response.streaming_content)
        self.assertEqual(payload, b'0123456789')
        self.assertEqual(response['X-Content-Type-Options'], 'nosniff')
        self.assertTrue(response['Content-Disposition'].startswith('attachment;'))
        self.assertIn('q%20report.pdf', response['Content-Disposition'])
        self.assertEqual(response['Content-Type'], 'application/pdf')

    def test_oversize_declared_content_length_refused_413(self):
        fake_client = _client_client(self.a_account)
        probe = MagicMock()
        probe.status_code = 200
        probe.headers = {'content-length': str(51 * 1024 * 1024),
                         'content-type': 'application/octet-stream'}
        with patch('mail.tasks._get_stalwart_client', return_value=fake_client), \
             self._cred(), \
             patch('httpx.head', return_value=probe):
            response = self.client.get(self.url, {'account_id': str(self.a_account.id)})
        self.assertEqual(response.status_code, status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)

    def test_provider_404_is_non_enumerating_404(self):
        fake_client = _client_client(self.a_account)
        probe = MagicMock()
        probe.status_code = 404
        probe.headers = {}
        with patch('mail.tasks._get_stalwart_client', return_value=fake_client), \
             self._cred(), \
             patch('httpx.head', return_value=probe):
            response = self.client.get(self.url, {'account_id': str(self.a_account.id)})
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_provider_auth_failure_is_502_without_secret_leak(self):
        fake_client = _client_client(self.a_account)
        probe = MagicMock()
        probe.status_code = 401
        probe.headers = {'content-type': 'text/html'}
        with patch('mail.tasks._get_stalwart_client', return_value=fake_client), \
             self._cred(), \
             patch('httpx.head', return_value=probe):
            response = self.client.get(self.url, {'account_id': str(self.a_account.id)})
        self.assertEqual(response.status_code, status.HTTP_502_BAD_GATEWAY)
        self.assertNotIn('Basic', response.json().get('error', ''))

    def test_filename_sanitized_in_disposition(self):
        fake_client = _client_client(self.a_account)
        probe = MagicMock()
        probe.status_code = 200
        probe.headers = {'content-length': '4', 'content-type': 'text/html'}

        captured = {}

        def fake_stream(method, url, headers=None, timeout=None, verify=None, follow_redirects=None):
            captured['url'] = url
            body = MagicMock()
            body.raise_for_status = lambda: None
            body.iter_bytes = lambda chunk_size=None: iter([b'evil'])
            return MagicMock(__enter__=lambda s: body, __exit__=lambda *a: False)

        with patch('mail.tasks._get_stalwart_client', return_value=fake_client), \
             self._cred(), \
             patch('httpx.head', return_value=probe), \
             patch('httpx.stream', side_effect=fake_stream):
            response = self.client.get(
                self.url,
                {'account_id': str(self.a_account.id),
                 'filename': '../../etc/passwd<script>'},
            )
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            self.assertNotIn('..', response['Content-Disposition'])
            self.assertNotIn('<', response['Content-Disposition'])
            # Consume the stream inside the patched context to capture the URL.
            b''.join(response.streaming_content)
        # Path components and hostile characters must not shape the upstream URL.
        self.assertNotIn('/etc/', captured['url'])
