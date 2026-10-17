"""
Unit tests for credential encryption service.
"""
import importlib
import json
import os
from io import StringIO
from unittest.mock import patch
from django.test import SimpleTestCase, TestCase, override_settings
from django.core.management import call_command, CommandError
from django.core.exceptions import ImproperlyConfigured

from mail.models import MailAccount
from mail.services.credential_service import (
    CredentialService,
    get_credential_service,
    generate_encryption_key,
    EncryptionKeyNotConfigured,
    DecryptionError,
)


class GenerateEncryptionKeyTest(TestCase):
    """Test encryption key generation."""

    def test_generate_key_returns_base64_string(self):
        """Test that generated key is a valid base64 string."""
        key = generate_encryption_key()

        self.assertIsInstance(key, str)
        # Base64 encoded 32-byte key should be 44 characters
        self.assertEqual(len(key), 44)

    def test_generate_key_unique(self):
        """Test that each generated key is unique."""
        keys = [generate_encryption_key() for _ in range(10)]
        self.assertEqual(len(set(keys)), 10)


class CredentialServiceInitTest(TestCase):
    """Test CredentialService initialization."""

    @override_settings(MAIL_CREDENTIAL_ENCRYPTION_KEY=None)
    def test_init_without_key_raises_error(self):
        """Test that missing encryption key raises EncryptionKeyNotConfigured."""
        # Clear the singleton instance
        CredentialService._instance = None

        with self.assertRaises(EncryptionKeyNotConfigured) as ctx:
            CredentialService()

        self.assertIn('MAIL_CREDENTIAL_ENCRYPTION_KEY', str(ctx.exception))

    @override_settings(MAIL_CREDENTIAL_ENCRYPTION_KEY='')
    def test_init_with_empty_key_raises_error(self):
        """Test that empty encryption key raises EncryptionKeyNotConfigured."""
        # Clear the singleton instance
        CredentialService._instance = None

        with self.assertRaises(EncryptionKeyNotConfigured):
            CredentialService()

    @override_settings(MAIL_CREDENTIAL_ENCRYPTION_KEY=generate_encryption_key())
    def test_init_with_valid_key_succeeds(self):
        """Test that valid encryption key initializes successfully."""
        CredentialService._instance = None
        service = CredentialService()
        self.assertIsNotNone(service._cipher)


class CredentialServiceEncryptionTest(TestCase):
    """Test encryption/decryption operations."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.test_key = generate_encryption_key()

    @override_settings(MAIL_CREDENTIAL_ENCRYPTION_KEY=None)
    def test_encrypt_password(self):
        """Test password encryption."""
        with override_settings(MAIL_CREDENTIAL_ENCRYPTION_KEY=self.test_key):
            service = CredentialService()
            password = "MySecretPassword123!"

            encrypted = service.encrypt_password(password)

            self.assertIsInstance(encrypted, bytes)
            self.assertNotEqual(encrypted, password.encode())
            self.assertGreater(len(encrypted), len(password))

    def test_decrypt_password(self):
        """Test password decryption."""
        with override_settings(MAIL_CREDENTIAL_ENCRYPTION_KEY=self.test_key):
            service = CredentialService()
            original = "MySecretPassword123!"

            encrypted = service.encrypt_password(original)
            decrypted = service.decrypt_password(encrypted)

            self.assertEqual(decrypted, original)

    def test_encrypt_empty_string(self):
        """Test encrypting empty password."""
        with override_settings(MAIL_CREDENTIAL_ENCRYPTION_KEY=self.test_key):
            service = CredentialService()

            encrypted = service.encrypt_password("")
            decrypted = service.decrypt_password(encrypted)

            self.assertEqual(decrypted, "")

    def test_encrypt_unicode_characters(self):
        """Test encrypting password with unicode characters."""
        with override_settings(MAIL_CREDENTIAL_ENCRYPTION_KEY=self.test_key):
            service = CredentialService()
            password = "P@ssw0rd日本語中文🔒"

            encrypted = service.encrypt_password(password)
            decrypted = service.decrypt_password(encrypted)

            self.assertEqual(decrypted, password)

    def test_encrypt_long_password(self):
        """Test encrypting very long password."""
        with override_settings(MAIL_CREDENTIAL_ENCRYPTION_KEY=self.test_key):
            service = CredentialService()
            password = "A" * 10000  # 10KB password

            encrypted = service.encrypt_password(password)
            decrypted = service.decrypt_password(encrypted)

            self.assertEqual(decrypted, password)

    def test_encrypt_produces_different_ciphertext(self):
        """Test that same password produces different ciphertext each time (due to IV)."""
        with override_settings(MAIL_CREDENTIAL_ENCRYPTION_KEY=self.test_key):
            service = CredentialService()
            password = "test123"

            encrypted1 = service.encrypt_password(password)
            encrypted2 = service.encrypt_password(password)

            self.assertNotEqual(encrypted1, encrypted2)

    def test_encrypt_password_returns_versioned_envelope(self):
        """MAIL-9: stored credentials are JSON envelopes, not raw Fernet tokens."""
        with override_settings(
            MAIL_CREDENTIAL_ENCRYPTION_KEY=self.test_key,
            MAIL_CREDENTIAL_KEY_ID='mail-credentials-test',
        ):
            service = CredentialService()
            encrypted = service.encrypt_password("envelope-secret")

            envelope = json.loads(encrypted.decode('utf-8'))
            self.assertEqual(envelope['version'], 1)
            self.assertEqual(envelope['alg'], 'fernet')
            self.assertEqual(envelope['key_id'], 'mail-credentials-test')
            self.assertIn('ciphertext', envelope)
            self.assertNotIn('envelope-secret', encrypted.decode('utf-8'))
            self.assertEqual(service.decrypt_password(encrypted), "envelope-secret")

    def test_migrate_legacy_raw_fernet_token_to_envelope(self):
        """MAIL-9: legacy raw Fernet tokens migrate into envelopes."""
        with override_settings(MAIL_CREDENTIAL_ENCRYPTION_KEY=self.test_key):
            service = CredentialService()
            legacy_token = service._cipher.encrypt(b"legacy-secret")

            migrated = service.migrate_stored_value(legacy_token)

            self.assertTrue(service.is_envelope(migrated))
            self.assertEqual(service.decrypt_password(migrated), "legacy-secret")
            self.assertNotIn(b"legacy-secret", migrated)

    def test_migrate_wrong_key_raw_fernet_token_fails_closed(self):
        """MAIL-9: wrong-key legacy tokens must not be re-encrypted as plaintext."""
        key1 = generate_encryption_key()
        key2 = generate_encryption_key()
        with override_settings(MAIL_CREDENTIAL_ENCRYPTION_KEY=key1):
            token = CredentialService()._cipher.encrypt(b"legacy-secret")

        with override_settings(MAIL_CREDENTIAL_ENCRYPTION_KEY=key2):
            service = CredentialService()
            with self.assertRaises(DecryptionError):
                service.migrate_stored_value(token)

    def test_dual_key_rotation_reencrypts_old_envelope_to_current_key(self):
        """MAIL-9: envelopes keep key_id and can rotate during a dual-key window."""
        old_key = generate_encryption_key()
        new_key = generate_encryption_key()
        with override_settings(
            MAIL_CREDENTIAL_ENCRYPTION_KEY=old_key,
            MAIL_CREDENTIAL_KEY_ID='mail-credentials-v1',
        ):
            old_service = CredentialService()
            old_envelope = old_service.encrypt_password("rotate-secret")

        with override_settings(
            MAIL_CREDENTIAL_ENCRYPTION_KEY=new_key,
            MAIL_CREDENTIAL_KEY_ID='mail-credentials-v2',
            MAIL_CREDENTIAL_ENCRYPTION_KEYS={
                'mail-credentials-v1': old_key,
                'mail-credentials-v2': new_key,
            },
        ):
            rotation_service = CredentialService()
            rotated = rotation_service.migrate_stored_value(old_envelope)
            envelope = json.loads(rotated.decode('utf-8'))
            self.assertEqual(envelope['key_id'], 'mail-credentials-v2')
            self.assertEqual(rotation_service.decrypt_password(rotated), "rotate-secret")

        with override_settings(
            MAIL_CREDENTIAL_ENCRYPTION_KEY=new_key,
            MAIL_CREDENTIAL_KEY_ID='mail-credentials-v2',
        ):
            new_only_service = CredentialService()
            self.assertEqual(new_only_service.decrypt_password(rotated), "rotate-secret")
            with self.assertRaises(DecryptionError):
                new_only_service.decrypt_password(old_envelope)


    def test_env_wired_dual_key_rotation_decrypts_old_envelope(self):
        """MAIL-9: prod settings wire MAIL_CREDENTIAL_ENCRYPTION_KEYS from env."""
        import mailmanager.settings as mail_settings

        old_key = generate_encryption_key()
        new_key = generate_encryption_key()
        with override_settings(
            MAIL_CREDENTIAL_ENCRYPTION_KEY=old_key,
            MAIL_CREDENTIAL_KEY_ID='mail-credentials-v1',
        ):
            old_envelope = CredentialService().encrypt_password("env-rotate-secret")

        env_mapping = json.dumps({
            'mail-credentials-v1': old_key,
            'mail-credentials-v2': new_key,
        })
        with patch.dict(os.environ, {
            'MAIL_CREDENTIAL_ENCRYPTION_KEY': new_key,
            'MAIL_CREDENTIAL_KEY_ID': 'mail-credentials-v2',
            'MAIL_CREDENTIAL_ENCRYPTION_KEYS': env_mapping,
        }):
            reloaded = importlib.reload(mail_settings)
            self.assertEqual(reloaded.MAIL_CREDENTIAL_ENCRYPTION_KEYS, env_mapping)
            with override_settings(
                MAIL_CREDENTIAL_ENCRYPTION_KEY=reloaded.MAIL_CREDENTIAL_ENCRYPTION_KEY,
                MAIL_CREDENTIAL_KEY_ID=reloaded.MAIL_CREDENTIAL_KEY_ID,
                MAIL_CREDENTIAL_ENCRYPTION_KEYS=reloaded.MAIL_CREDENTIAL_ENCRYPTION_KEYS,
            ):
                rotation_service = CredentialService()
                self.assertEqual(rotation_service.decrypt_password(old_envelope), "env-rotate-secret")
                rotated = rotation_service.migrate_stored_value(old_envelope)
                self.assertEqual(rotation_service.decrypt_password(rotated), "env-rotate-secret")
                self.assertEqual(json.loads(rotated.decode('utf-8'))['key_id'], 'mail-credentials-v2')
        importlib.reload(mail_settings)

    def test_migrate_brace_prefixed_plaintext_value_to_envelope(self):
        """MAIL-9: plaintext like `{secret}` is not rejected as a bad envelope."""
        with override_settings(MAIL_CREDENTIAL_ENCRYPTION_KEY=self.test_key):
            service = CredentialService()
            migrated = service.migrate_stored_value(b"{secret}")

            self.assertTrue(service.is_envelope(migrated))
            self.assertEqual(service.decrypt_password(migrated), "{secret}")

    def test_migrate_json_plaintext_with_version_only_to_envelope(self):
        """MAIL-9: arbitrary JSON-ish plaintext is not mistaken for an envelope."""
        with override_settings(MAIL_CREDENTIAL_ENCRYPTION_KEY=self.test_key):
            service = CredentialService()
            plaintext = b'{"version":1}'

            migrated = service.migrate_stored_value(plaintext)

            self.assertTrue(service.is_envelope(migrated))
            self.assertEqual(service.decrypt_password(migrated), plaintext.decode('utf-8'))

    def test_migrate_malformed_envelope_fails_closed(self):
        """MAIL-9: envelope-shaped corrupted values are not re-wrapped."""
        with override_settings(MAIL_CREDENTIAL_ENCRYPTION_KEY=self.test_key):
            service = CredentialService()

            with self.assertRaises(DecryptionError):
                service.migrate_stored_value(
                    b'{"version":1,"alg":"fernet","key_id":"mail-credentials-v1"}'
                )

    def test_migrate_plaintext_value_to_envelope(self):
        """MAIL-9: plaintext DB leftovers migrate without exposing the value."""
        with override_settings(MAIL_CREDENTIAL_ENCRYPTION_KEY=self.test_key):
            service = CredentialService()
            migrated = service.migrate_stored_value(b"plain-secret")

            self.assertTrue(service.is_envelope(migrated))
            self.assertEqual(service.decrypt_password(migrated), "plain-secret")
            self.assertNotIn(b"plain-secret", migrated)

    def test_decrypt_with_wrong_key_fails(self):
        """Test that decryption with wrong key fails."""
        key1 = generate_encryption_key()
        key2 = generate_encryption_key()

        with override_settings(MAIL_CREDENTIAL_ENCRYPTION_KEY=key1):
            service1 = CredentialService()
            encrypted = service1.encrypt_password("secret")

        with override_settings(MAIL_CREDENTIAL_ENCRYPTION_KEY=key2):
            service2 = CredentialService()
            with self.assertRaises(Exception):
                service2.decrypt_password(encrypted)


class CredentialServiceCanDecryptTest(TestCase):
    """Test can_decrypt method."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.test_key = generate_encryption_key()

    def test_can_decrypt_valid_data(self):
        """Test can_decrypt returns True for valid encrypted data."""
        with override_settings(MAIL_CREDENTIAL_ENCRYPTION_KEY=self.test_key):
            service = CredentialService()
            encrypted = service.encrypt_password("test")

            self.assertTrue(service.can_decrypt(encrypted))

    def test_can_decrypt_invalid_data(self):
        """Test can_decrypt returns False for invalid encrypted data."""
        with override_settings(MAIL_CREDENTIAL_ENCRYPTION_KEY=self.test_key):
            CredentialService._instance = None
            service = CredentialService()

            # Invalid encrypted data should return False
            self.assertFalse(service.can_decrypt(b"invalid_encrypted_data"))
            self.assertFalse(service.can_decrypt(b"12345"))
            self.assertFalse(service.can_decrypt(b"not_valid_fernet_token"))

    def test_can_decrypt_empty_returns_true(self):
        """Test can_decrypt returns True for empty data (empty password is valid)."""
        with override_settings(MAIL_CREDENTIAL_ENCRYPTION_KEY=self.test_key):
            CredentialService._instance = None
            service = CredentialService()

            # Empty data represents empty password, which is valid
            self.assertTrue(service.can_decrypt(b""))
            self.assertTrue(service.can_decrypt(None))


class GetCredentialServiceTest(TestCase):
    """Test get_credential_service factory function."""

    def test_get_credential_service_returns_singleton(self):
        """Test that get_credential_service returns cached instance."""
        key = generate_encryption_key()

        with override_settings(MAIL_CREDENTIAL_ENCRYPTION_KEY=key):
            # Clear any cached instance
            CredentialService._instance = None

            service1 = get_credential_service()
            service2 = get_credential_service()

            # Should be same instance
            self.assertIs(service1, service2)

            # Clean up
            CredentialService._instance = None

    def test_get_credential_service_without_key_raises_error(self):
        """Test that get_credential_service raises error without key."""
        with override_settings(MAIL_CREDENTIAL_ENCRYPTION_KEY=None):
            # Clear the singleton instance
            CredentialService._instance = None

            with self.assertRaises(EncryptionKeyNotConfigured):
                get_credential_service()


class CredentialMigrationCommandTest(TestCase):
    """MAIL-9 migration output must be redacted."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.test_key = generate_encryption_key()

    def test_migration_redacts_plaintext_values(self):
        with override_settings(MAIL_CREDENTIAL_ENCRYPTION_KEY=self.test_key):
            account = MailAccount.objects.create(
                user_id=99100,
                email_address='migrate@example.com',
                incoming_host='imap.example.com',
                outgoing_host='smtp.example.com',
                incoming_username='migrate@example.com',
                encrypted_incoming_password=b'plaintext-secret-fixture',
            )
            stdout = StringIO()
            call_command('migrate_mail_credentials', stdout=stdout)

            output = stdout.getvalue()
            self.assertIn(f'migrated account {account.id}', output)
            self.assertNotIn('plaintext-secret-fixture', output)

            account.refresh_from_db()
            service = CredentialService()
            self.assertTrue(service.is_envelope(account.encrypted_incoming_password))
            self.assertEqual(service.decrypt_password(account.encrypted_incoming_password), 'plaintext-secret-fixture')

    def test_migration_does_not_clobber_concurrent_password_update(self):
        """MAIL-9: migration write is conditioned on the original DB value."""
        with override_settings(MAIL_CREDENTIAL_ENCRYPTION_KEY=self.test_key):
            account = MailAccount.objects.create(
                user_id=99101,
                email_address='concurrent@example.com',
                incoming_host='imap.example.com',
                outgoing_host='smtp.example.com',
                incoming_username='concurrent@example.com',
                encrypted_incoming_password=b'old-plaintext-secret',
            )
            service = CredentialService()
            concurrent_value = service.encrypt_password('concurrent-new-secret')
            original_filter = MailAccount.objects.filter

            def racing_filter(*args, **kwargs):
                queryset = original_filter(*args, **kwargs)
                if (kwargs.get('pk') == account.pk and
                        bytes(kwargs.get('encrypted_incoming_password') or b'') == b'old-plaintext-secret'):
                    original_filter(pk=account.pk).update(
                        encrypted_incoming_password=concurrent_value,
                    )
                return queryset

            stdout = StringIO()
            stderr = StringIO()
            with patch.object(MailAccount.objects, 'filter', side_effect=racing_filter):
                with self.assertRaises(CommandError):
                    call_command('migrate_mail_credentials', stdout=stdout, stderr=stderr)

            self.assertNotIn('old-plaintext-secret', stdout.getvalue())
            self.assertIn('credential changed during migration', stderr.getvalue())
            account.refresh_from_db()
            self.assertEqual(service.decrypt_password(account.encrypted_incoming_password), 'concurrent-new-secret')

    def test_migration_preflight_aborts_before_partial_wrong_key_writes(self):
        """MAIL-9: wrong-key canary validates all credentials before any writes."""
        old_key = generate_encryption_key()
        new_key = generate_encryption_key()
        with override_settings(MAIL_CREDENTIAL_ENCRYPTION_KEY=old_key):
            wrong_key_token = CredentialService()._cipher.encrypt(b'old-encrypted-secret')

        encrypted_account = MailAccount.objects.create(
            user_id=99102,
            email_address='wrong-key@example.com',
            incoming_host='imap.example.com',
            outgoing_host='smtp.example.com',
            incoming_username='wrong-key@example.com',
            encrypted_incoming_password=wrong_key_token,
        )
        plaintext_account = MailAccount.objects.create(
            user_id=99103,
            email_address='plaintext-after-wrong-key@example.com',
            incoming_host='imap.example.com',
            outgoing_host='smtp.example.com',
            incoming_username='plaintext-after-wrong-key@example.com',
            encrypted_incoming_password=b'plaintext-must-not-be-rewritten',
        )

        stdout = StringIO()
        stderr = StringIO()
        with override_settings(MAIL_CREDENTIAL_ENCRYPTION_KEY=new_key):
            with self.assertRaises(CommandError) as ctx:
                call_command('migrate_mail_credentials', stdout=stdout, stderr=stderr)
            service = CredentialService()
            plaintext_account.refresh_from_db()
            encrypted_account.refresh_from_db()

            self.assertIn('preflight failed', str(ctx.exception))
            self.assertFalse(service.is_envelope(plaintext_account.encrypted_incoming_password))
            self.assertEqual(bytes(plaintext_account.encrypted_incoming_password), b'plaintext-must-not-be-rewritten')
            self.assertEqual(bytes(encrypted_account.encrypted_incoming_password), wrong_key_token)


class MailRedactionUtilityTest(SimpleTestCase):
    """MAIL-13 redaction utility covers logs, provider errors, and validation data."""

    def test_redacts_tokens_oauth_codes_connection_strings_and_emails(self):
        from mail.redaction import REDACTED, redact_sensitive

        secret = 'abcdefghijklmnopqrstuvwxyzABCDEF1234567890'
        value = (
            'user=owner@example.com access_token=%s code=oauth-code-123 '
            'postgres://mail:%s@db.example.com/mail Authorization: Bearer %s'
        ) % (secret, secret, secret)

        redacted = redact_sensitive(value)

        self.assertNotIn(secret, redacted)
        self.assertNotIn('owner@example.com', redacted)
        self.assertIn(REDACTED, redacted)
        self.assertIn('<email:', redacted)

    def test_logging_filter_redacts_short_credential_assignment(self):
        import logging

        from mail.redaction import MailRedactionFilter

        stream = StringIO()
        handler = logging.StreamHandler(stream)
        handler.addFilter(MailRedactionFilter())
        log = logging.getLogger('mail.tests.credential-redaction-boundary')
        old_handlers = log.handlers[:]
        old_propagate = log.propagate
        old_level = log.level
        log.handlers = [handler]
        log.propagate = False
        log.setLevel(logging.INFO)

        try:
            log.info('provider failed credential=%s credentials=%s', 'short-secret', 'other-secret')
        finally:
            log.handlers = old_handlers
            log.propagate = old_propagate
            log.setLevel(old_level)

        output = stream.getvalue()
        self.assertNotIn('short-secret', output)
        self.assertNotIn('other-secret', output)
        self.assertIn('credential=<redacted>', output)
        self.assertIn('credentials=<redacted>', output)

    def test_redacts_validation_error_payloads(self):
        from rest_framework.exceptions import ValidationError
        from mail.exceptions import redacting_exception_handler

        secret = 'validation-secret-token-abcdefghijklmnopqrstuvwxyz'
        response = redacting_exception_handler(
            ValidationError({'incoming_password': [secret], 'settings': {'api_key': secret}}),
            {},
        )

        self.assertIsNotNone(response)
        self.assertNotIn(secret, str(response.data))
        self.assertIn('<redacted>', str(response.data))

    def test_provider_exception_text_is_secret_safe(self):
        from mail.services.mail_service import MailServiceError

        secret = 'provider-refresh-token-abcdefghijklmnopqrstuvwxyz'
        exc = MailServiceError(f'provider body access_token={secret} owner=owner@example.com')

        self.assertNotIn(secret, str(exc))
        self.assertNotIn('owner@example.com', str(exc))
        self.assertIn('<redacted>', str(exc))

    def test_logging_filter_redacts_message_args_and_extra_fields(self):
        import logging
        import uuid

        from mail.redaction import MailRedactionFilter

        stream = StringIO()
        handler = logging.StreamHandler(stream)
        handler.addFilter(MailRedactionFilter())
        handler.setFormatter(logging.Formatter('%(message)s %(body)s %(headers)s %(account_id)s'))
        log = logging.getLogger('mail.tests.redaction-boundary')
        old_handlers = log.handlers[:]
        old_propagate = log.propagate
        old_level = log.level
        log.handlers = [handler]
        log.propagate = False
        log.setLevel(logging.INFO)
        secret = 'provider-access-token-abcdefghijklmnopqrstuvwxyz'
        account_id = uuid.UUID('12345678-1234-4234-9234-123456789abc')

        try:
            log.info(
                'sync failed token=%s account=%s',
                secret,
                account_id,
                extra={
                    'body': {'refresh_token': secret, 'owner': 'owner@example.com'},
                    'headers': {'Authorization': f'Bearer {secret}'},
                    'account_id': account_id,
                },
            )
        finally:
            log.handlers = old_handlers
            log.propagate = old_propagate
            log.setLevel(old_level)

        output = stream.getvalue()
        self.assertNotIn(secret, output)
        self.assertNotIn(str(account_id), output)
        self.assertNotIn('owner@example.com', output)
        self.assertIn('<redacted>', output)
        self.assertIn('<id:', output)
        self.assertIn('<email:', output)

    def test_settings_install_redaction_filter_on_console_handler(self):
        from django.conf import settings

        logging_config = settings.LOGGING
        self.assertEqual(
            logging_config['filters']['mail_redaction']['()'],
            'mail.redaction.MailRedactionFilter',
        )
        self.assertIn('mail_redaction', logging_config['handlers']['console']['filters'])
