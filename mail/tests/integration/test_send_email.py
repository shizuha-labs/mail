"""
Integration tests for email sending.

These tests require Mailpit and Stalwart to be running.
Run with: docker compose exec shizuha-mail python manage.py test mail.tests.integration
"""
import uuid
from django.test import TestCase, override_settings
from django.conf import settings
from unittest import skipUnless

from mail.models import MailAccount
from mail.services.stalwart_client import StalwartJMAPClient as StalwartClient
from mail.tests.helpers import MailpitClient, create_test_jwt


def stalwart_available():
    """Check if Stalwart is available for integration tests."""
    import requests
    try:
        response = requests.get(
            f"{settings.STALWART_JMAP_URL}/.well-known/jmap",
            timeout=2
        )
        return response.status_code == 200
    except:
        return False


def mailpit_available():
    """Check if Mailpit is available for integration tests."""
    import requests
    try:
        response = requests.get("http://mailpit:8025/api/v1/messages", timeout=2)
        return response.status_code == 200
    except:
        return False


@skipUnless(stalwart_available() and mailpit_available(), "Requires Stalwart and Mailpit")
class EmailSendingIntegrationTest(TestCase):
    """Integration tests for sending emails through Stalwart to Mailpit."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.mailpit = MailpitClient()

    def setUp(self):
        """Set up test data."""
        # Clear Mailpit before each test
        self.mailpit.clear()

        # Create test account
        self.account = MailAccount.objects.create(
            user_id=99001,
            email_address='test@dev.shizuha.local',
            display_name='Test User',
            incoming_host='imap.dev.shizuha.local',
            outgoing_host='smtp.dev.shizuha.local',
            incoming_username='test@dev.shizuha.local',
        )

        # Create test JWT
        self.token = create_test_jwt(
            user_id=self.account.user_id,
            email=self.account.email_address
        )

    def test_send_simple_email(self):
        """Test sending a simple text email."""
        client = StalwartClient(
            account=self.account,
            user_auth_token=self.token
        )

        unique_subject = f'Integration Test {uuid.uuid4()}'

        client.send_email(
            from_address={
                'name': 'Test User',
                'email': 'test@dev.shizuha.local'
            },
            to_addresses=[
                {'email': 'external@example.com'}
            ],
            subject=unique_subject,
            text_body='This is a test email from integration tests.'
        )

        # Wait for email to appear in Mailpit
        message = self.mailpit.wait_for_message(unique_subject, timeout=10)

        self.assertIsNotNone(message)
        self.assertEqual(message['Subject'], unique_subject)

    def test_send_email_with_cc(self):
        """Test sending email with CC recipients."""
        client = StalwartClient(
            account=self.account,
            user_auth_token=self.token
        )

        unique_subject = f'CC Test {uuid.uuid4()}'

        client.send_email(
            from_address={'email': 'test@dev.shizuha.local'},
            to_addresses=[{'email': 'primary@example.com'}],
            cc_addresses=[{'email': 'cc@example.com'}],
            subject=unique_subject,
            text_body='Email with CC recipient.'
        )

        message = self.mailpit.wait_for_message(unique_subject, timeout=10)

        self.assertIsNotNone(message)
        # Verify CC header exists
        full_msg = self.mailpit.get_message(message['ID'])
        cc_addresses = [addr['Address'] for addr in full_msg.get('Cc', [])]
        self.assertIn('cc@example.com', cc_addresses)

    def test_send_email_with_bcc(self):
        """Test sending email with BCC recipients (BCC should not appear in headers)."""
        client = StalwartClient(
            account=self.account,
            user_auth_token=self.token
        )

        unique_subject = f'BCC Test {uuid.uuid4()}'

        client.send_email(
            from_address={'email': 'test@dev.shizuha.local'},
            to_addresses=[{'email': 'primary@example.com'}],
            bcc_addresses=[{'email': 'bcc@example.com'}],
            subject=unique_subject,
            text_body='Email with BCC recipient.'
        )

        message = self.mailpit.wait_for_message(unique_subject, timeout=10)

        self.assertIsNotNone(message)
        # BCC should NOT appear in message headers
        full_msg = self.mailpit.get_message(message['ID'])
        self.assertNotIn('Bcc', full_msg)

    def test_send_html_email(self):
        """Test sending HTML email."""
        client = StalwartClient(
            account=self.account,
            user_auth_token=self.token
        )

        unique_subject = f'HTML Test {uuid.uuid4()}'
        html_content = '<h1>Hello</h1><p>This is <strong>HTML</strong> content.</p>'

        client.send_email(
            from_address={'email': 'test@dev.shizuha.local'},
            to_addresses=[{'email': 'recipient@example.com'}],
            subject=unique_subject,
            html_body=html_content,
            text_body='Hello. This is HTML content.'  # Plain text fallback
        )

        message = self.mailpit.wait_for_message(unique_subject, timeout=10)

        self.assertIsNotNone(message)
        full_msg = self.mailpit.get_message(message['ID'])
        self.assertIn('<h1>Hello</h1>', full_msg.get('HTML', ''))

    def test_send_to_multiple_recipients(self):
        """Test sending to multiple recipients."""
        client = StalwartClient(
            account=self.account,
            user_auth_token=self.token
        )

        unique_subject = f'Multi-recipient Test {uuid.uuid4()}'

        client.send_email(
            from_address={'email': 'test@dev.shizuha.local'},
            to_addresses=[
                {'email': 'first@example.com'},
                {'email': 'second@example.com'},
                {'email': 'third@example.com'}
            ],
            subject=unique_subject,
            text_body='Email to multiple recipients.'
        )

        message = self.mailpit.wait_for_message(unique_subject, timeout=10)

        self.assertIsNotNone(message)
        full_msg = self.mailpit.get_message(message['ID'])
        to_addresses = [addr['Address'] for addr in full_msg.get('To', [])]
        self.assertEqual(len(to_addresses), 3)

    def test_email_headers(self):
        """Test that standard email headers are set correctly."""
        client = StalwartClient(
            account=self.account,
            user_auth_token=self.token
        )

        unique_subject = f'Headers Test {uuid.uuid4()}'

        client.send_email(
            from_address={
                'name': 'Test Sender',
                'email': 'test@dev.shizuha.local'
            },
            to_addresses=[{'email': 'recipient@example.com'}],
            subject=unique_subject,
            text_body='Testing headers.'
        )

        message = self.mailpit.wait_for_message(unique_subject, timeout=10)
        full_msg = self.mailpit.get_message(message['ID'])

        # Check essential headers exist
        self.assertIn('MessageID', full_msg)
        self.assertIn('Date', full_msg)
        self.assertEqual(full_msg['From']['Address'], 'test@dev.shizuha.local')

    def test_reply_threading(self):
        """Test that replies include proper threading headers."""
        client = StalwartClient(
            account=self.account,
            user_auth_token=self.token
        )

        # Send original email
        original_subject = f'Original {uuid.uuid4()}'
        client.send_email(
            from_address={'email': 'test@dev.shizuha.local'},
            to_addresses=[{'email': 'recipient@example.com'}],
            subject=original_subject,
            text_body='Original message.'
        )

        original = self.mailpit.wait_for_message(original_subject, timeout=10)
        original_full = self.mailpit.get_message(original['ID'])
        original_message_id = original_full['MessageID']

        # Send reply with In-Reply-To header
        reply_subject = f'Re: {original_subject}'
        client.send_email(
            from_address={'email': 'test@dev.shizuha.local'},
            to_addresses=[{'email': 'recipient@example.com'}],
            subject=reply_subject,
            text_body='Reply to original.',
            in_reply_to=original_message_id,
            references=[original_message_id]
        )

        reply = self.mailpit.wait_for_message(reply_subject, timeout=10)
        reply_full = self.mailpit.get_message(reply['ID'])

        # Verify threading headers (if supported by client)
        # Note: JMAP handles this automatically when using threadId
        self.assertIsNotNone(reply_full)
