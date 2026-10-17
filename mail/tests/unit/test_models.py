"""
Unit tests for mail models.
"""
from django.test import TestCase
from mail.models import MailAccount, MailFolder, MailMessage, MailThread


class MailAccountTestCase(TestCase):
    """Tests for MailAccount model."""

    def test_create_account(self):
        """Test creating a mail account."""
        account = MailAccount.objects.create(
            user_id=12345,
            email_address='test@shizuha.com',
            display_name='Test User',
            incoming_host='imap.example.com',
            outgoing_host='smtp.example.com',
            incoming_username='test@shizuha.com',
        )

        self.assertEqual(account.email_address, 'test@shizuha.com')
        self.assertEqual(account.display_name, 'Test User')
        self.assertFalse(account.is_default)
        self.assertEqual(account.incoming_server_type, 'imap')

    def test_account_string_representation(self):
        """Test account __str__ method."""
        account = MailAccount.objects.create(
            user_id=12346,
            email_address='test@shizuha.com',
            incoming_host='imap.example.com',
            outgoing_host='smtp.example.com',
            incoming_username='test@shizuha.com',
        )

        # __str__ now includes user ID
        self.assertEqual(str(account), 'test@shizuha.com (user 12346)')

    def test_default_account_toggle(self):
        """Test that only one account per user can be default."""
        account1 = MailAccount.objects.create(
            user_id=12347,
            email_address='test1@shizuha.com',
            incoming_host='imap.example.com',
            outgoing_host='smtp.example.com',
            incoming_username='test1@shizuha.com',
            is_default=True,
        )

        account2 = MailAccount.objects.create(
            user_id=12347,
            email_address='test2@shizuha.com',
            incoming_host='imap.example.com',
            outgoing_host='smtp.example.com',
            incoming_username='test2@shizuha.com',
            is_default=True,
        )

        # Refresh account1 from DB
        account1.refresh_from_db()

        # Only account2 should be default now
        self.assertFalse(account1.is_default)
        self.assertTrue(account2.is_default)


class MailFolderTestCase(TestCase):
    """Tests for MailFolder model."""

    def setUp(self):
        self.account = MailAccount.objects.create(
            user_id=12348,
            email_address='test@shizuha.com',
            incoming_host='imap.example.com',
            outgoing_host='smtp.example.com',
            incoming_username='test@shizuha.com',
        )

    def test_create_folder(self):
        """Test creating a mail folder."""
        folder = MailFolder.objects.create(
            account=self.account,
            name='Inbox',
            role='inbox',
            jmap_mailbox_id='inbox-001'
        )

        self.assertEqual(folder.name, 'Inbox')
        self.assertEqual(folder.role, 'inbox')
        self.assertEqual(folder.total_messages, 0)

    def test_nested_folder(self):
        """Test creating nested folders."""
        parent = MailFolder.objects.create(
            account=self.account,
            name='Projects',
            role='custom',
            jmap_mailbox_id='projects-001'
        )

        child = MailFolder.objects.create(
            account=self.account,
            name='Work',
            role='custom',
            parent=parent,
            jmap_mailbox_id='work-001'
        )

        self.assertEqual(child.parent, parent)
        self.assertIn(child, parent.children.all())


class MailMessageTestCase(TestCase):
    """Tests for MailMessage model."""

    def setUp(self):
        self.account = MailAccount.objects.create(
            user_id=12349,
            email_address='test@shizuha.com',
            incoming_host='imap.example.com',
            outgoing_host='smtp.example.com',
            incoming_username='test@shizuha.com',
        )
        self.folder = MailFolder.objects.create(
            account=self.account,
            name='Inbox',
            role='inbox',
            jmap_mailbox_id='inbox-002'
        )

    def test_create_message(self):
        """Test creating a mail message."""
        message = MailMessage.objects.create(
            account=self.account,
            folder=self.folder,
            subject='Test Subject',
            from_address='sender@example.com',
            from_name='Sender',
            to_addresses=[{'email': 'test@shizuha.com'}],
            preview='This is a test...',
            jmap_email_id='email-001'
        )

        self.assertEqual(message.subject, 'Test Subject')
        self.assertTrue(message.is_unread)
        self.assertFalse(message.is_flagged)

    def test_message_in_thread(self):
        """Test message threading."""
        thread = MailThread.objects.create(
            account=self.account,
            subject='Thread Subject',
            jmap_thread_id='thread-001'
        )

        message = MailMessage.objects.create(
            account=self.account,
            folder=self.folder,
            thread=thread,
            subject='Re: Thread Subject',
            from_address='sender@example.com',
            to_addresses=[{'email': 'test@shizuha.com'}],
            jmap_email_id='email-002'
        )

        self.assertEqual(message.thread, thread)
        self.assertIn(message, thread.messages.all())


class MailThreadTestCase(TestCase):
    """Tests for MailThread model."""

    def setUp(self):
        self.account = MailAccount.objects.create(
            user_id=12350,
            email_address='test@shizuha.com',
            incoming_host='imap.example.com',
            outgoing_host='smtp.example.com',
            incoming_username='test@shizuha.com',
        )

    def test_create_thread(self):
        """Test creating a mail thread."""
        thread = MailThread.objects.create(
            account=self.account,
            subject='Discussion Thread',
            jmap_thread_id='thread-002'
        )

        self.assertEqual(thread.subject, 'Discussion Thread')
        self.assertEqual(thread.message_count, 0)
        self.assertEqual(thread.unread_count, 0)

    def test_thread_participants(self):
        """Test thread participants."""
        thread = MailThread.objects.create(
            account=self.account,
            subject='Multi-participant Thread',
            jmap_thread_id='thread-003',
            participants=[
                {'email': 'user1@example.com'},
                {'email': 'user2@example.com'}
            ]
        )

        self.assertEqual(len(thread.participants), 2)
