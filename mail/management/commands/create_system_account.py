"""
Management command to create or update the system email account.

The system account is used for sending system emails (password resets, notifications, etc.)
without requiring user-specific authentication.
"""
import getpass
from django.core.management.base import BaseCommand, CommandError
from django.conf import settings
from mail.models import MailAccount
from mail.services.credential_service import get_credential_service


class Command(BaseCommand):
    help = 'Create or update the system email account for sending internal emails'

    def add_arguments(self, parser):
        parser.add_argument(
            '--email',
            type=str,
            help='Email address for the system account (default: from SYSTEM_EMAIL_ADDRESS setting)',
        )
        parser.add_argument(
            '--user-id',
            type=int,
            help='User ID to associate with the account (default: from SYSTEM_USER_ID setting)',
        )
        parser.add_argument(
            '--password',
            type=str,
            help='Password for the email account (will prompt if not provided)',
        )
        parser.add_argument(
            '--incoming-host',
            type=str,
            help='IMAP/JMAP server hostname',
        )
        parser.add_argument(
            '--incoming-port',
            type=int,
            default=993,
            help='IMAP/JMAP server port (default: 993)',
        )
        parser.add_argument(
            '--incoming-type',
            type=str,
            choices=['imap', 'jmap'],
            default='imap',
            help='Incoming server type (default: imap)',
        )
        parser.add_argument(
            '--outgoing-host',
            type=str,
            help='SMTP server hostname',
        )
        parser.add_argument(
            '--outgoing-port',
            type=int,
            default=587,
            help='SMTP server port (default: 587)',
        )
        parser.add_argument(
            '--security',
            type=str,
            choices=['ssl', 'starttls', 'none'],
            default='ssl',
            help='Security type for incoming connection (default: ssl)',
        )
        parser.add_argument(
            '--outgoing-security',
            type=str,
            choices=['ssl', 'starttls', 'none'],
            default='starttls',
            help='Security type for outgoing connection (default: starttls)',
        )
        parser.add_argument(
            '--test',
            action='store_true',
            help='Test the connection after creating/updating the account',
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='Update existing account even if it exists',
        )

    def handle(self, *args, **options):
        email = options.get('email') or getattr(settings, 'SYSTEM_EMAIL_ADDRESS', 'noreply@shizuha.com')
        user_id = options.get('user_id') or getattr(settings, 'SYSTEM_USER_ID', 1)

        self.stdout.write(f"Setting up system email account: {email}")

        # Check if account exists
        existing = MailAccount.objects.filter(
            email_address=email,
            user_id=user_id
        ).first()

        if existing and not options['force']:
            self.stdout.write(self.style.WARNING(
                f"System account already exists: {email} (use --force to update)"
            ))
            return

        # Get password
        password = options.get('password')
        if not password:
            password = getpass.getpass(f"Enter password for {email}: ")
            if not password:
                raise CommandError("Password is required")

        # Get server settings
        incoming_host = options.get('incoming_host')
        outgoing_host = options.get('outgoing_host')

        if not incoming_host:
            # Try to infer from email domain
            domain = email.split('@')[1] if '@' in email else ''
            if not domain:
                raise CommandError("Could not determine domain from email address")

            # Check if we have a configured Stalwart
            stalwart_url = getattr(settings, 'STALWART_JMAP_URL', '')
            if stalwart_url:
                # Extract host from URL
                from urllib.parse import urlparse
                parsed = urlparse(stalwart_url)
                incoming_host = parsed.hostname
                outgoing_host = outgoing_host or parsed.hostname
                self.stdout.write(f"Using Stalwart server: {incoming_host}")
            else:
                incoming_host = input(f"Enter IMAP/JMAP server hostname: ")

        if not outgoing_host:
            outgoing_host = incoming_host

        # Encrypt password
        try:
            credential_service = get_credential_service()
        except Exception as e:
            raise CommandError(
                f"Failed to initialize credential service: {e}\n"
                "Make sure MAIL_CREDENTIAL_ENCRYPTION_KEY is set in environment."
            )

        encrypted_password = credential_service.encrypt_password(password)

        # Create or update account
        if existing:
            self.stdout.write("Updating existing account...")
            existing.incoming_server_type = options['incoming_type']
            existing.incoming_host = incoming_host
            existing.incoming_port = options['incoming_port']
            existing.incoming_security = options['security']
            existing.incoming_username = email
            existing.encrypted_incoming_password = encrypted_password
            existing.outgoing_host = outgoing_host
            existing.outgoing_port = options['outgoing_port']
            existing.outgoing_security = options['outgoing_security']
            existing.outgoing_username = email
            existing.encrypted_outgoing_password = encrypted_password
            existing.display_name = 'System Notifications'
            existing.from_name = 'Shizuha'
            existing.is_active = True
            existing.save()
            account = existing
        else:
            self.stdout.write("Creating new account...")
            account = MailAccount.objects.create(
                user_id=user_id,
                email_address=email,
                display_name='System Notifications',
                from_name='Shizuha',
                incoming_server_type=options['incoming_type'],
                incoming_host=incoming_host,
                incoming_port=options['incoming_port'],
                incoming_security=options['security'],
                incoming_username=email,
                encrypted_incoming_password=encrypted_password,
                outgoing_host=outgoing_host,
                outgoing_port=options['outgoing_port'],
                outgoing_security=options['outgoing_security'],
                outgoing_username=email,
                encrypted_outgoing_password=encrypted_password,
                outgoing_requires_auth=True,
                is_active=True,
                is_default=True,
            )

        self.stdout.write(self.style.SUCCESS(f"System account created/updated: {email}"))

        # Test connection if requested
        if options['test']:
            self.stdout.write("Testing connection...")
            from mail.services.mail_service import get_mail_service

            mail_service = get_mail_service()
            results = mail_service.test_connection(account)

            if results['incoming']['success']:
                self.stdout.write(self.style.SUCCESS(f"  Incoming: OK"))
            else:
                self.stdout.write(self.style.ERROR(f"  Incoming: FAILED - {results['incoming']['message']}"))

            if results['outgoing']['success']:
                self.stdout.write(self.style.SUCCESS(f"  Outgoing: OK"))
            else:
                self.stdout.write(self.style.ERROR(f"  Outgoing: FAILED - {results['outgoing']['message']}"))

            if not results['incoming']['success'] or not results['outgoing']['success']:
                raise CommandError("Connection test failed")

        self.stdout.write(self.style.SUCCESS("\nSystem email account is ready!"))
        self.stdout.write(f"  Email: {email}")
        self.stdout.write(f"  User ID: {user_id}")
        self.stdout.write(f"  Incoming: {incoming_host}:{options['incoming_port']} ({options['incoming_type']}/{options['security']})")
        self.stdout.write(f"  Outgoing: {outgoing_host}:{options['outgoing_port']} ({options['outgoing_security']})")
