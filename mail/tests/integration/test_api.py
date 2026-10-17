"""
Integration tests for REST API endpoints.

These tests verify the API authentication and basic CRUD operations.
Note: Tests that require actual mail server connections are in E2E tests.
"""
import uuid
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch, MagicMock
from mail.tests.active_identity import ActiveIdentityTestCase as TestCase
from rest_framework.test import APIClient
from rest_framework import status

from mail.models import MailAccount, MailFolder, MailMessage, MailThread
from mail.tests.helpers import create_test_jwt


class APIAuthenticationTest(TestCase):
    """Test API authentication."""

    def setUp(self):
        self.client = APIClient()
        self.user_id = 10001
        self.account = MailAccount.objects.create(
            user_id=self.user_id,
            email_address='apitest@shizuha.com',
            incoming_host='imap.example.com',
            outgoing_host='smtp.example.com',
            incoming_username='apitest@shizuha.com',
        )

    def test_unauthenticated_request_rejected(self):
        """Test that unauthenticated requests are rejected."""
        response = self.client.get('/api/accounts/')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_authenticated_request_allowed(self):
        """Test that authenticated requests are allowed."""
        token = create_test_jwt(
            user_id=self.user_id,
            email=self.account.email_address
        )
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')

        response = self.client.get('/api/accounts/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)


class OAuthBFFLoginTest(TestCase):
    """The public login endpoint remains fail-closed and base-path correct."""

    def setUp(self):
        self.client = APIClient()

    @patch.dict('os.environ', {'SHIZUHA_OAUTH_CLIENT_ID': ''})
    def test_missing_oauth_client_stays_fail_closed(self):
        response = self.client.get('/api/auth/oauth/login/')

        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertEqual(response.json(), {'error': 'oauth_not_configured'})

    @patch.dict('os.environ', {
        'SHIZUHA_OAUTH_CLIENT_ID': 'mail-client-id',
        'SHIZUHA_OAUTH_AUTHORIZE_URL': 'https://shizuha.com/id/oauth/authorize',
        'MAIL_PUBLIC_URL': 'https://shizuha.com/mail',
    })
    def test_configured_login_uses_pkce_and_exact_public_callback(self):
        response = self.client.get('/api/auth/oauth/login/')

        self.assertEqual(response.status_code, status.HTTP_302_FOUND)
        redirect = urlparse(response['Location'])
        params = parse_qs(redirect.query)
        self.assertEqual(
            f'{redirect.scheme}://{redirect.netloc}{redirect.path}',
            'https://shizuha.com/id/oauth/authorize',
        )
        self.assertEqual(params['client_id'], ['mail-client-id'])
        self.assertEqual(
            params['redirect_uri'],
            ['https://shizuha.com/mail/api/auth/oauth/callback/'],
        )
        self.assertEqual(params['response_type'], ['code'])
        self.assertEqual(params['code_challenge_method'], ['S256'])
        self.assertTrue(params['code_challenge'][0])
        self.assertTrue(params['state'][0])

        state_cookie = response.cookies['shizuha_mail_oauth_state']
        self.assertTrue(state_cookie['httponly'])
        self.assertTrue(state_cookie['secure'])
        self.assertEqual(state_cookie['samesite'], 'Lax')


class AccountAPITest(TestCase):
    """Test account API endpoints."""

    def setUp(self):
        self.client = APIClient()
        self.user_id = 10002
        self.account = MailAccount.objects.create(
            user_id=self.user_id,
            email_address='account@shizuha.com',
            display_name='Account Test User',
            incoming_host='imap.example.com',
            outgoing_host='smtp.example.com',
            incoming_username='account@shizuha.com',
        )
        token = create_test_jwt(
            user_id=self.user_id,
            email=self.account.email_address
        )
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')

    def test_list_accounts(self):
        """Test listing user's accounts."""
        response = self.client.get('/api/accounts/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)

    def test_get_my_account(self):
        """Test getting current user's primary account."""
        response = self.client.get('/api/accounts/me/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['email_address'], 'account@shizuha.com')

    def test_get_account_detail(self):
        """Test getting account by ID."""
        response = self.client.get(f'/api/accounts/{self.account.id}/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['email_address'], 'account@shizuha.com')

    def test_delete_account(self):
        """Test deleting an account."""
        response = self.client.delete(f'/api/accounts/{self.account.id}/')
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(MailAccount.objects.filter(id=self.account.id).exists())

    def test_cross_owner_account_detail_matches_nonexistent_404_shape(self):
        """Cross-account and nonexistent account lookups must not enumerate owners."""
        other = MailAccount.objects.create(
            user_id=99999,
            email_address='other-owner@shizuha.com',
            incoming_host='imap.example.com',
            outgoing_host='smtp.example.com',
            incoming_username='other-owner@shizuha.com',
        )
        missing = uuid.uuid4()

        cross = self.client.get(f'/api/accounts/{other.id}/')
        nonexistent = self.client.get(f'/api/accounts/{missing}/')

        self.assertEqual(cross.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(nonexistent.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(cross.data, nonexistent.data)

    def test_cross_owner_sync_does_not_enqueue_jobs(self):
        """Client-supplied account_id cannot enqueue sync for another owner."""
        other = MailAccount.objects.create(
            user_id=99998,
            email_address='sync-other@shizuha.com',
            incoming_host='imap.example.com',
            outgoing_host='smtp.example.com',
            incoming_username='sync-other@shizuha.com',
        )

        with patch('mail.tasks.sync_mailbox_counts.delay') as counts_delay, \
             patch('mail.tasks.sync_new_messages.delay') as messages_delay:
            response = self.client.post('/api/accounts/sync/', {'account_id': str(other.id)}, format='json')

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(response.data, {'error': 'Mail account not found'})
        counts_delay.assert_not_called()
        messages_delay.assert_not_called()

    def test_sync_enqueue_uses_internal_account_pk_and_server_owner(self):
        """Queued sync jobs carry the server-resolved owner, not a client override."""
        with patch('mail.tasks.sync_mailbox_counts.delay') as counts_delay, \
             patch('mail.tasks.sync_new_messages.delay') as messages_delay:
            response = self.client.post('/api/accounts/sync/', {
                'account_id': str(self.account.id),
                'user_id': 99999,
            }, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        counts_delay.assert_called_once_with(str(self.account.id), expected_user_id=self.user_id)
        messages_delay.assert_called_once_with(str(self.account.id), expected_user_id=self.user_id)


class SyncJobAuthorizationTest(TestCase):
    """Background jobs re-check ownership/active state at execution time."""

    def setUp(self):
        self.account = MailAccount.objects.create(
            user_id=12001,
            email_address='job-owner@shizuha.com',
            incoming_host='imap.example.com',
            outgoing_host='smtp.example.com',
            incoming_username='job-owner@shizuha.com',
            is_active=True,
        )

    def test_sync_job_skips_owner_mismatch(self):
        from mail.tasks import sync_new_messages

        with patch('mail.tasks._sync_imap_messages') as sync_impl:
            result = sync_new_messages(str(self.account.id), expected_user_id=99999)

        self.assertEqual(result, 0)
        sync_impl.assert_not_called()

    def test_sync_job_skips_revoked_account_before_execution(self):
        from mail.tasks import sync_new_messages

        self.account.is_active = False
        self.account.save(update_fields=['is_active'])

        with patch('mail.tasks._sync_imap_messages') as sync_impl:
            result = sync_new_messages(str(self.account.id), expected_user_id=self.account.user_id)

        self.assertEqual(result, 0)
        sync_impl.assert_not_called()

    def test_sync_job_suppresses_side_effects_if_account_revoked_mid_sync(self):
        from mail.tasks import sync_new_messages

        def revoke_during_sync(account, folder_id=None, since=None):
            MailAccount.objects.filter(id=account.id).update(is_active=False)
            return 5

        with patch('mail.tasks._sync_imap_messages', side_effect=revoke_during_sync), \
             patch('mail.tasks._notify_daemon_new_messages') as notify:
            result = sync_new_messages(str(self.account.id), expected_user_id=self.account.user_id)

        self.assertEqual(result, 0)
        notify.assert_not_called()


class ThreadAPITest(TestCase):
    """Test thread API endpoints (read-only)."""

    def setUp(self):
        self.client = APIClient()
        self.user_id = 10005
        self.account = MailAccount.objects.create(
            user_id=self.user_id,
            email_address='thread@shizuha.com',
            incoming_host='imap.example.com',
            outgoing_host='smtp.example.com',
            incoming_username='thread@shizuha.com',
        )
        token = create_test_jwt(
            user_id=self.user_id,
            email=self.account.email_address
        )
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')

        self.inbox = MailFolder.objects.create(
            account=self.account,
            name='Inbox',
            role='inbox',
            jmap_mailbox_id=f'inbox-{uuid.uuid4()}'
        )

        self.sent = MailFolder.objects.create(
            account=self.account,
            name='Sent',
            role='sent',
            jmap_mailbox_id=f'sent-{uuid.uuid4()}'
        )

        self.thread = MailThread.objects.create(
            account=self.account,
            subject='Test Thread',
            message_count=2,
            jmap_thread_id=f'thread-{uuid.uuid4()}'
        )

        MailMessage.objects.create(
            account=self.account,
            folder=self.inbox,
            thread=self.thread,
            subject='Test Thread',
            from_address='sender@example.com',
            to_addresses=[{'email': 'thread@shizuha.com'}],
            jmap_email_id=f'email-{uuid.uuid4()}'
        )
        MailMessage.objects.create(
            account=self.account,
            folder=self.inbox,
            thread=self.thread,
            subject='Re: Test Thread',
            from_address='thread@shizuha.com',
            to_addresses=[{'email': 'sender@example.com'}],
            jmap_email_id=f'email-{uuid.uuid4()}'
        )

        self.sent_thread = MailThread.objects.create(
            account=self.account,
            subject='Sent Thread',
            message_count=1,
            jmap_thread_id=f'thread-{uuid.uuid4()}'
        )
        MailMessage.objects.create(
            account=self.account,
            folder=self.sent,
            thread=self.sent_thread,
            subject='Sent Thread',
            from_address='thread@shizuha.com',
            to_addresses=[{'email': 'recipient@example.com'}],
            jmap_email_id=f'email-{uuid.uuid4()}'
        )

    def test_list_threads(self):
        """Test listing threads."""
        response = self.client.get('/api/threads/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_get_thread(self):
        """Test getting a thread with messages."""
        response = self.client.get(f'/api/threads/{self.thread.id}/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['subject'], 'Test Thread')
        self.assertEqual(len(response.data['messages']), 2)

    def test_list_threads_scoped_by_folder_role(self):
        """MAIL-92: folder_role filters threads by the canonical system folder."""
        response = self.client.get('/api/threads/', {'folder_role': 'inbox'})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        subjects = {t['subject'] for t in response.data['results']}
        self.assertEqual(subjects, {'Test Thread'})

        response = self.client.get('/api/threads/', {'folder_role': 'sent'})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        subjects = {t['subject'] for t in response.data['results']}
        self.assertEqual(subjects, {'Sent Thread'})

    def test_list_threads_scoped_by_folder_uuid(self):
        """MAIL-92: folder=<MailFolder UUID> filters threads in that folder."""
        response = self.client.get('/api/threads/', {'folder': str(self.sent.id)})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        subjects = {t['subject'] for t in response.data['results']}
        self.assertEqual(subjects, {'Sent Thread'})

    def test_list_threads_scoped_by_server_mailbox_id(self):
        """MAIL-92: folder=<jmap_mailbox_id> filters threads in that mailbox."""
        response = self.client.get('/api/threads/', {'folder': self.inbox.jmap_mailbox_id})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        subjects = {t['subject'] for t in response.data['results']}
        self.assertEqual(subjects, {'Test Thread'})

    def test_list_threads_unknown_folder_role_is_explicit_400(self):
        """MAIL-92: an unknown folder_role is an error, never an unfiltered set."""
        response = self.client.get('/api/threads/', {'folder_role': 'archive'})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('error', response.data)

    def test_list_threads_unknown_folder_is_explicit_400(self):
        """MAIL-92: an unknown folder id is an error, never an unfiltered set."""
        response = self.client.get(
            '/api/threads/', {'folder': '00000000-0000-4000-8000-000000000000'}
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('error', response.data)


class EmailProviderAPITest(TestCase):
    """Test email provider preset API."""

    def setUp(self):
        self.client = APIClient()
        self.user_id = 10007
        self.account = MailAccount.objects.create(
            user_id=self.user_id,
            email_address='provider@shizuha.com',
            incoming_host='imap.example.com',
            outgoing_host='smtp.example.com',
            incoming_username='provider@shizuha.com',
        )
        token = create_test_jwt(
            user_id=self.user_id,
            email=self.account.email_address
        )
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')

    def test_list_providers(self):
        """Test listing email provider presets."""
        response = self.client.get('/api/providers/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsInstance(response.data, list)
        # Should have common providers
        provider_ids = [p['id'] for p in response.data]
        self.assertIn('gmail', provider_ids)
        self.assertIn('outlook', provider_ids)
        stalwart = next(p for p in response.data if p['id'] == 'stalwart')
        self.assertEqual(stalwart['incoming_host'], 'mail-shadow.shizuha.com')
        self.assertEqual(stalwart['incoming_port'], 443)
        self.assertEqual(stalwart['incoming_server_type'], 'jmap')
        self.assertEqual(stalwart['outgoing_host'], 'mail-shadow.shizuha.com')
        self.assertEqual(stalwart['outgoing_port'], 587)

    def test_detect_gmail_provider(self):
        """Test detecting Gmail provider from email."""
        response = self.client.get('/api/providers/detect/?email=user@gmail.com')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['id'], 'gmail')

    def test_detect_outlook_provider(self):
        """Test detecting Outlook provider from email."""
        response = self.client.get('/api/providers/detect/?email=user@outlook.com')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['id'], 'outlook')

    def test_detect_custom_provider(self):
        """Test detecting custom provider for unknown domain."""
        response = self.client.get('/api/providers/detect/?email=user@unknown-domain.xyz')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['id'], 'custom')


class ContactAPITest(TestCase):
    """Test contact API endpoints."""

    def setUp(self):
        self.client = APIClient()
        self.user_id = 10008
        self.account = MailAccount.objects.create(
            user_id=self.user_id,
            email_address='contact@shizuha.com',
            incoming_host='imap.example.com',
            outgoing_host='smtp.example.com',
            incoming_username='contact@shizuha.com',
        )
        token = create_test_jwt(
            user_id=self.user_id,
            email=self.account.email_address
        )
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')

    def test_list_contacts(self):
        """Test listing contacts."""
        response = self.client.get('/api/contacts/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_create_contact(self):
        """Test creating a contact."""
        response = self.client.post('/api/contacts/', {
            'email': 'friend@example.com',
            'display_name': 'Test Friend'
        })
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['email'], 'friend@example.com')


class FilterAPITest(TestCase):
    """Test filter (mail rules) API endpoints."""

    def setUp(self):
        self.client = APIClient()
        self.user_id = 10009
        self.account = MailAccount.objects.create(
            user_id=self.user_id,
            email_address='filter@shizuha.com',
            incoming_host='imap.example.com',
            outgoing_host='smtp.example.com',
            incoming_username='filter@shizuha.com',
        )
        token = create_test_jwt(
            user_id=self.user_id,
            email=self.account.email_address
        )
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')

    def test_list_filters(self):
        """Test listing mail filters."""
        response = self.client.get('/api/filters/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)


class MessageCreateFailSoftTest(TestCase):
    """MAIL-159: POST /api/messages/ must fail-soft with JSON 400, never an HTML 500.

    MailMessageViewSet is a ModelViewSet whose list serializer has only read-only
    fields; DRF's default create used to pass empty validation and then raise an
    unhandled IntegrityError on an empty body, surfacing a raw Django HTML 500
    page. The create override returns a structured JSON 400 instead.
    """

    def setUp(self):
        self.client = APIClient()
        self.user_id = 10010
        self.account = MailAccount.objects.create(
            user_id=self.user_id,
            email_address='msgcreate@shizuha.com',
            incoming_host='imap.example.com',
            outgoing_host='smtp.example.com',
            incoming_username='msgcreate@shizuha.com',
        )
        token = create_test_jwt(
            user_id=self.user_id,
            email=self.account.email_address
        )
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')

    def test_empty_body_returns_json_400_not_html_500(self):
        """Empty-body POST must return structured JSON 400 (MAIL-57 check 6)."""
        response = self.client.post(
            '/api/messages/',
            data={},
            content_type='application/json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        # Must be JSON, not an HTML error page.
        self.assertEqual(response['Content-Type'].split(';')[0], 'application/json')
        self.assertIn('error', response.json())
        self.assertNotIn('<html', response.content.decode('utf-8', errors='replace').lower())

    def test_invalid_json_control_returns_json_400(self):
        """Malformed JSON body must also fail-soft with JSON 400 (control)."""
        response = self.client.post(
            '/api/messages/',
            data='{not valid json',
            content_type='application/json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response['Content-Type'].split(';')[0], 'application/json')
        self.assertNotIn('<html', response.content.decode('utf-8', errors='replace').lower())

    def test_unauthenticated_create_still_rejected(self):
        """Unauthenticated create must remain 401 (auth is not bypassed)."""
        anon = APIClient()
        response = anon.post('/api/messages/', data={}, content_type='application/json')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
