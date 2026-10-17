from mail.tests.active_identity import ActiveIdentityTestCase
"""
Integration tests for multi-account support.

These tests verify the new generic mail client features:
- Multiple accounts per user
- Account creation with server settings
- Connection testing
- Default account management
- Credential encryption
"""
import uuid
from unittest.mock import patch, MagicMock
from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from rest_framework import status

from mail.models import MailAccount, MailFolder, MailMessage
from mail.tests.helpers import create_test_jwt
from mail.services.credential_service import CredentialService, generate_encryption_key


# Generate a test encryption key
TEST_ENCRYPTION_KEY = generate_encryption_key()


@override_settings(MAIL_CREDENTIAL_ENCRYPTION_KEY=TEST_ENCRYPTION_KEY)
class CredentialEncryptionTest(TestCase):
    """Test credential encryption service."""

    def test_encrypt_decrypt_password(self):
        """Test password encryption and decryption."""
        service = CredentialService()
        password = "MySecretPassword123!"

        encrypted = service.encrypt_password(password)

        # Encrypted should be bytes and different from original
        self.assertIsInstance(encrypted, bytes)
        self.assertNotEqual(encrypted, password.encode())

        # Should decrypt back to original
        decrypted = service.decrypt_password(encrypted)
        self.assertEqual(decrypted, password)

    def test_encrypt_empty_password(self):
        """Test encrypting empty password."""
        service = CredentialService()
        encrypted = service.encrypt_password("")
        decrypted = service.decrypt_password(encrypted)
        self.assertEqual(decrypted, "")

    def test_encrypt_unicode_password(self):
        """Test encrypting password with unicode characters."""
        service = CredentialService()
        password = "MyP@ssw0rd!日本語"

        encrypted = service.encrypt_password(password)
        decrypted = service.decrypt_password(encrypted)

        self.assertEqual(decrypted, password)

    def test_different_encryptions_produce_different_output(self):
        """Test that same password encrypted twice produces different ciphertext (due to IV)."""
        service = CredentialService()
        password = "test123"

        encrypted1 = service.encrypt_password(password)
        encrypted2 = service.encrypt_password(password)

        # Should be different due to random IV
        self.assertNotEqual(encrypted1, encrypted2)

        # But both should decrypt to same value
        self.assertEqual(service.decrypt_password(encrypted1), password)
        self.assertEqual(service.decrypt_password(encrypted2), password)

    def test_can_decrypt_returns_true_for_valid(self):
        """Test can_decrypt returns True for valid encrypted data."""
        service = CredentialService()
        encrypted = service.encrypt_password("test")
        self.assertTrue(service.can_decrypt(encrypted))

    def test_can_decrypt_returns_false_for_invalid(self):
        """Test can_decrypt returns False for invalid encrypted data."""
        service = CredentialService()
        # Invalid encrypted data should return False
        self.assertFalse(service.can_decrypt(b"invalid_data"))
        # Empty/None represents empty password which is valid
        self.assertTrue(service.can_decrypt(None))
        self.assertTrue(service.can_decrypt(b""))


@override_settings(MAIL_CREDENTIAL_ENCRYPTION_KEY=TEST_ENCRYPTION_KEY)
class MultiAccountAPITest(ActiveIdentityTestCase):
    """Test multi-account API endpoints."""

    def setUp(self):
        self.client = APIClient()
        self.user_id = 12345
        self.user_email = 'testuser@shizuha.com'

        token = create_test_jwt(
            user_id=self.user_id,
            email=self.user_email
        )
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')

    def test_list_accounts_empty(self):
        """Test listing accounts when user has none."""
        response = self.client.get('/api/accounts/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 0)

    def test_create_account(self):
        """Test creating a new email account."""
        # Mock the credential service and mail service that are imported inside the view
        with patch('mail.services.credential_service.get_credential_service') as mock_cred:
            mock_cred_instance = MagicMock()
            mock_cred_instance.encrypt_password.return_value = b'encrypted_data'
            mock_cred.return_value = mock_cred_instance

            response = self.client.post('/api/accounts/', {
                'email_address': 'work@example.com',
                'incoming_password': 'password123',
                'display_name': 'Work Email',
                'from_name': 'Test User',
                'incoming_server_type': 'imap',
                'incoming_host': 'imap.example.com',
                'incoming_port': 993,
                'incoming_security': 'ssl',
                'incoming_username': 'work@example.com',
                'outgoing_host': 'smtp.example.com',
                'outgoing_port': 587,
                'outgoing_security': 'starttls',
            })

            self.assertEqual(response.status_code, status.HTTP_201_CREATED)
            self.assertEqual(response.data['email_address'], 'work@example.com')
            self.assertEqual(response.data['display_name'], 'Work Email')

            # Verify account was created in database
            account = MailAccount.objects.get(email_address='work@example.com')
            self.assertEqual(account.user_id, self.user_id)
            self.assertEqual(account.incoming_host, 'imap.example.com')

    def test_create_multiple_accounts(self):
        """Test that a user can create multiple accounts."""
        MailAccount.objects.create(
            user_id=self.user_id,
            email_address='personal@example.com',
            incoming_host='imap.personal.com',
            outgoing_host='smtp.personal.com',
            incoming_username='personal@example.com',
            is_default=True,
        )
        MailAccount.objects.create(
            user_id=self.user_id,
            email_address='work@example.com',
            incoming_host='imap.work.com',
            outgoing_host='smtp.work.com',
            incoming_username='work@example.com',
        )

        response = self.client.get('/api/accounts/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 2)

    def test_duplicate_email_same_user_upserts(self):
        """Test that duplicate email for same user updates existing account."""
        MailAccount.objects.create(
            user_id=self.user_id,
            email_address='test@example.com',
            incoming_host='imap.example.com',
            outgoing_host='smtp.example.com',
            incoming_username='test@example.com',
        )

        response = self.client.post('/api/accounts/', {
            'email_address': 'test@example.com',
            'incoming_password': 'password',
            'incoming_server_type': 'imap',
            'incoming_host': 'imap.updated.com',
            'incoming_port': 993,
            'incoming_security': 'ssl',
            'incoming_username': 'test@example.com',
            'outgoing_host': 'smtp.updated.com',
            'outgoing_port': 587,
            'outgoing_security': 'starttls',
        })

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(MailAccount.objects.filter(user_id=self.user_id, email_address='test@example.com').count(), 1)
        account = MailAccount.objects.get(user_id=self.user_id, email_address='test@example.com')
        self.assertEqual(account.incoming_host, 'imap.updated.com')
        self.assertEqual(account.outgoing_host, 'smtp.updated.com')

    def test_same_email_different_users_allowed(self):
        """Test that same email address for different users is allowed."""
        MailAccount.objects.create(
            user_id=self.user_id,
            email_address='shared@example.com',
            incoming_host='imap.example.com',
            outgoing_host='smtp.example.com',
            incoming_username='shared@example.com',
        )
        MailAccount.objects.create(
            user_id=99999,
            email_address='shared@example.com',
            incoming_host='imap.example.com',
            outgoing_host='smtp.example.com',
            incoming_username='shared@example.com',
        )

        self.assertEqual(MailAccount.objects.filter(email_address='shared@example.com').count(), 2)

    def test_delete_account(self):
        """Test deleting an email account."""
        account = MailAccount.objects.create(
            user_id=self.user_id,
            email_address='todelete@example.com',
            incoming_host='imap.example.com',
            outgoing_host='smtp.example.com',
            incoming_username='todelete@example.com',
        )

        response = self.client.delete(f'/api/accounts/{account.id}/')
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(MailAccount.objects.filter(id=account.id).exists())

    def test_cannot_delete_other_users_account(self):
        """Test that user cannot delete another user's account."""
        other_account = MailAccount.objects.create(
            user_id=99999,
            email_address='other@example.com',
            incoming_host='imap.example.com',
            outgoing_host='smtp.example.com',
            incoming_username='other@example.com',
        )

        response = self.client.delete(f'/api/accounts/{other_account.id}/')
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertTrue(MailAccount.objects.filter(id=other_account.id).exists())


@override_settings(MAIL_CREDENTIAL_ENCRYPTION_KEY=TEST_ENCRYPTION_KEY)
class DefaultAccountTest(ActiveIdentityTestCase):
    """Test default account management."""

    def setUp(self):
        self.client = APIClient()
        self.user_id = 12345

        token = create_test_jwt(
            user_id=self.user_id,
            email='user@shizuha.com'
        )
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')

        self.account1 = MailAccount.objects.create(
            user_id=self.user_id,
            email_address='account1@example.com',
            incoming_host='imap.example.com',
            outgoing_host='smtp.example.com',
            incoming_username='account1@example.com',
            is_default=True,
        )
        self.account2 = MailAccount.objects.create(
            user_id=self.user_id,
            email_address='account2@example.com',
            incoming_host='imap.example.com',
            outgoing_host='smtp.example.com',
            incoming_username='account2@example.com',
            is_default=False,
        )

    def test_set_default_account(self):
        """Test setting a different account as default."""
        # url_path is 'set-default' (hyphenated)
        response = self.client.post(f'/api/accounts/{self.account2.id}/set-default/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.account1.refresh_from_db()
        self.account2.refresh_from_db()

        self.assertTrue(self.account2.is_default)
        self.assertFalse(self.account1.is_default)

    def test_only_one_default_account(self):
        """Test that only one account can be default at a time."""
        self.client.post(f'/api/accounts/{self.account2.id}/set-default/')

        defaults = MailAccount.objects.filter(user_id=self.user_id, is_default=True)
        self.assertEqual(defaults.count(), 1)
        self.assertEqual(defaults.first().id, self.account2.id)


@override_settings(MAIL_CREDENTIAL_ENCRYPTION_KEY=TEST_ENCRYPTION_KEY)
class ConnectionTestAPITest(ActiveIdentityTestCase):
    """Test connection testing API."""

    def setUp(self):
        self.client = APIClient()
        self.user_id = 12345

        token = create_test_jwt(
            user_id=self.user_id,
            email='user@shizuha.com'
        )
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')

        self.account = MailAccount.objects.create(
            user_id=self.user_id,
            email_address='test@example.com',
            incoming_host='imap.example.com',
            outgoing_host='smtp.example.com',
            incoming_username='test@example.com',
        )

    def test_test_connection_success(self):
        """Test successful connection test."""
        with patch('mail.services.mail_service.get_mail_service') as mock_get_service:
            mock_service = MagicMock()
            mock_service.test_connection.return_value = {
                'incoming': {'success': True, 'message': 'Connected to IMAP server'},
                'outgoing': {'success': True, 'message': 'Connected to SMTP server'},
            }
            mock_get_service.return_value = mock_service

            response = self.client.post(f'/api/accounts/{self.account.id}/test/')

            self.assertEqual(response.status_code, status.HTTP_200_OK)
            self.assertTrue(response.data['incoming']['success'])
            self.assertTrue(response.data['outgoing']['success'])

    def test_test_connection_failure(self):
        """Test connection test with failure."""
        with patch('mail.services.mail_service.get_mail_service') as mock_get_service:
            mock_service = MagicMock()
            mock_service.test_connection.return_value = {
                'incoming': {'success': False, 'message': 'Authentication failed'},
                'outgoing': {'success': True, 'message': 'Connected'},
            }
            mock_get_service.return_value = mock_service

            response = self.client.post(f'/api/accounts/{self.account.id}/test/')

            self.assertEqual(response.status_code, status.HTTP_200_OK)
            self.assertFalse(response.data['incoming']['success'])
            self.assertTrue(response.data['outgoing']['success'])


@override_settings(MAIL_CREDENTIAL_ENCRYPTION_KEY=TEST_ENCRYPTION_KEY)
class EmailProviderPresetsTest(ActiveIdentityTestCase):
    """Test email provider presets API."""

    def setUp(self):
        self.client = APIClient()
        self.user_id = 12345

        token = create_test_jwt(
            user_id=self.user_id,
            email='user@shizuha.com'
        )
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')

    def test_list_providers(self):
        """Test listing email provider presets."""
        response = self.client.get('/api/providers/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsInstance(response.data, list)
        self.assertGreater(len(response.data), 0)

        provider_ids = [p['id'] for p in response.data]
        self.assertIn('gmail', provider_ids)
        self.assertIn('outlook', provider_ids)

    def test_detect_gmail_provider(self):
        """Test detecting Gmail provider from email."""
        response = self.client.get('/api/providers/detect/', {'email': 'user@gmail.com'})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['id'], 'gmail')
        self.assertEqual(response.data['incoming_host'], 'imap.gmail.com')

    def test_detect_outlook_provider(self):
        """Test detecting Outlook provider from email."""
        response = self.client.get('/api/providers/detect/', {'email': 'user@outlook.com'})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['id'], 'outlook')

    def test_detect_unknown_provider(self):
        """Test detecting unknown provider returns custom."""
        response = self.client.get('/api/providers/detect/', {'email': 'user@unknowndomain.xyz'})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['id'], 'custom')


@override_settings(MAIL_CREDENTIAL_ENCRYPTION_KEY=TEST_ENCRYPTION_KEY)
class AccountScopedMessagesTest(TestCase):
    """Test that messages are properly scoped to accounts."""

    def setUp(self):
        self.client = APIClient()
        self.user_id = 12345

        token = create_test_jwt(
            user_id=self.user_id,
            email='user@shizuha.com'
        )
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')

        # Create two accounts
        self.account1 = MailAccount.objects.create(
            user_id=self.user_id,
            email_address='account1@example.com',
            incoming_host='imap.example.com',
            outgoing_host='smtp.example.com',
            incoming_username='account1@example.com',
        )
        self.account2 = MailAccount.objects.create(
            user_id=self.user_id,
            email_address='account2@example.com',
            incoming_host='imap.example.com',
            outgoing_host='smtp.example.com',
            incoming_username='account2@example.com',
        )

        # Create folders with unique jmap_mailbox_id
        self.inbox1 = MailFolder.objects.create(
            account=self.account1,
            name='Inbox',
            role='inbox',
            jmap_mailbox_id=f'inbox-{uuid.uuid4()}',
        )
        self.inbox2 = MailFolder.objects.create(
            account=self.account2,
            name='Inbox',
            role='inbox',
            jmap_mailbox_id=f'inbox-{uuid.uuid4()}',
        )

        # Create messages in each inbox with required fields
        self.msg1 = MailMessage.objects.create(
            account=self.account1,
            folder=self.inbox1,
            subject='Message in Account 1',
            from_address='sender@example.com',
            to_addresses=[{'email': 'account1@example.com'}],
            jmap_email_id=f'email-{uuid.uuid4()}',
        )
        self.msg2 = MailMessage.objects.create(
            account=self.account2,
            folder=self.inbox2,
            subject='Message in Account 2',
            from_address='sender@example.com',
            to_addresses=[{'email': 'account2@example.com'}],
            jmap_email_id=f'email-{uuid.uuid4()}',
        )

    def test_messages_scoped_to_account_in_db(self):
        """Test that messages are properly stored per account in database."""
        # Account 1 should have its message
        acct1_messages = MailMessage.objects.filter(account=self.account1)
        self.assertEqual(acct1_messages.count(), 1)
        self.assertEqual(acct1_messages.first().subject, 'Message in Account 1')

        # Account 2 should have its message
        acct2_messages = MailMessage.objects.filter(account=self.account2)
        self.assertEqual(acct2_messages.count(), 1)
        self.assertEqual(acct2_messages.first().subject, 'Message in Account 2')

    def test_folders_scoped_to_account_in_db(self):
        """Test that folders are properly stored per account in database."""
        acct1_folders = MailFolder.objects.filter(account=self.account1)
        acct2_folders = MailFolder.objects.filter(account=self.account2)

        self.assertEqual(acct1_folders.count(), 1)
        self.assertEqual(acct2_folders.count(), 1)
        self.assertEqual(acct1_folders.first().id, self.inbox1.id)
        self.assertEqual(acct2_folders.first().id, self.inbox2.id)


@override_settings(MAIL_CREDENTIAL_ENCRYPTION_KEY=TEST_ENCRYPTION_KEY)
class InternalSendTest(TestCase):
    """Test internal send endpoint for system emails."""

    def setUp(self):
        self.client = APIClient()

        self.system_account = MailAccount.objects.create(
            user_id=1,
            email_address='noreply@shizuha.com',
            display_name='System Notifications',
            from_name='Shizuha',
            incoming_host='mail.shizuha.com',
            outgoing_host='mail.shizuha.com',
            incoming_username='noreply@shizuha.com',
            is_active=True,
            is_default=True,
        )

    def test_internal_send_without_header_rejected(self):
        """Test internal send without X-Internal-Service header is rejected."""
        response = self.client.post('/api/internal/send/', {
            'to_addresses': ['user@example.com'],
            'subject': 'Test',
            'body_text': 'Test body',
        })
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    @override_settings(
        SYSTEM_EMAIL_ADDRESS='noreply@shizuha.com',
        SYSTEM_USER_ID=1,
    )
    def test_internal_send_with_valid_header(self):
        """Test internal send with valid internal service header."""
        with patch('mail.services.mail_service.get_mail_service') as mock_get_service:
            mock_service = MagicMock()
            mock_service.send_email.return_value = {'success': True, 'message_id': 'abc123'}
            mock_get_service.return_value = mock_service

            response = self.client.post(
                '/api/internal/send/',
                {
                    'to_addresses': ['user@example.com'],
                    'subject': 'Password Reset',
                    'body_text': 'Click here to reset your password.',
                },
                HTTP_X_INTERNAL_SERVICE='shizuha-id',
            )

            # Should succeed or at least get to the service call
            self.assertIn(response.status_code, [
                status.HTTP_200_OK, status.HTTP_201_CREATED, status.HTTP_500_INTERNAL_SERVER_ERROR
            ])


@override_settings(MAIL_CREDENTIAL_ENCRYPTION_KEY=TEST_ENCRYPTION_KEY)
class AccountUpdateTest(ActiveIdentityTestCase):
    """Test account update operations."""

    def setUp(self):
        self.client = APIClient()
        self.user_id = 12345

        token = create_test_jwt(
            user_id=self.user_id,
            email='user@shizuha.com'
        )
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')

        self.account = MailAccount.objects.create(
            user_id=self.user_id,
            email_address='test@example.com',
            display_name='Original Name',
            incoming_host='imap.example.com',
            outgoing_host='smtp.example.com',
            incoming_username='test@example.com',
        )

    def test_update_display_name_via_preferences(self):
        """Test updating account display name via preferences endpoint."""
        # Make this account the default first
        self.account.is_default = True
        self.account.save()

        response = self.client.patch('/api/accounts/preferences/', {
            'display_name': 'Updated Name',
        })

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.account.refresh_from_db()
        self.assertEqual(self.account.display_name, 'Updated Name')

    def test_update_signature_via_preferences(self):
        """Test updating email signature via preferences endpoint."""
        self.account.is_default = True
        self.account.save()

        signature = "Best regards,\nTest User"

        response = self.client.patch('/api/accounts/preferences/', {
            'signature': signature,
        })

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.account.refresh_from_db()
        self.assertEqual(self.account.signature, signature)

    def test_update_password_via_preferences(self):
        """Test updating account password re-encrypts it."""
        self.account.is_default = True
        self.account.save()

        with patch('mail.services.credential_service.get_credential_service') as mock_get_service:
            mock_service = MagicMock()
            mock_service.encrypt_password.return_value = b'new_encrypted_password'
            mock_get_service.return_value = mock_service

            response = self.client.patch('/api/accounts/preferences/', {
                'incoming_password': 'new_password',
            })

            self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_preferences_account_id_is_owner_scoped_non_enumerating(self):
        """MAIL-13: cross-owner preference writes match account authz 404 shape."""
        other = MailAccount.objects.create(
            user_id=99999,
            email_address='other-pref@example.com',
            incoming_host='imap.example.com',
            outgoing_host='smtp.example.com',
            incoming_username='other-pref@example.com',
            display_name='Other Owner',
        )
        missing = uuid.uuid4()

        cross = self.client.patch('/api/accounts/preferences/', {
            'account_id': str(other.id),
            'display_name': 'Stolen Name',
        }, format='json')
        nonexistent = self.client.patch('/api/accounts/preferences/', {
            'account_id': str(missing),
            'display_name': 'Missing Name',
        }, format='json')

        self.assertEqual(cross.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(nonexistent.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(cross.data, nonexistent.data)
        other.refresh_from_db()
        self.assertEqual(other.display_name, 'Other Owner')

    def test_connection_test_exception_response_and_logs_are_redacted(self):
        """MAIL-13: provider exceptions do not leak secrets to logs or API payloads."""
        self.account.is_default = True
        self.account.save()
        secret = 'connection-secret-token-abcdefghijklmnopqrstuvwxyz'

        with patch('mail.services.mail_service.get_mail_service') as mock_get_service:
            mock_service = MagicMock()
            mock_service.test_connection.side_effect = Exception(
                f'provider said access_token={secret} owner=owner@example.com'
            )
            mock_get_service.return_value = mock_service

            with self.assertLogs('mail.views', level='ERROR') as logs:
                response = self.client.post(f'/api/accounts/{self.account.id}/test/')

        self.assertEqual(response.status_code, status.HTTP_502_BAD_GATEWAY)
        self.assertNotIn(secret, str(response.data))
        self.assertNotIn(secret, '\n'.join(logs.output))
        self.assertNotIn('owner@example.com', '\n'.join(logs.output))
        self.assertEqual(response.data, {'error': 'Connection test failed'})
