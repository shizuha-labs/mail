"""
Management command to sync data from Stalwart Mail Server.
"""
from django.core.management.base import BaseCommand
from django.conf import settings


class Command(BaseCommand):
    help = 'Sync folders and messages from Stalwart Mail Server'

    def add_arguments(self, parser):
        parser.add_argument(
            '--account',
            help='Specific account email to sync (default: all)'
        )
        parser.add_argument(
            '--folders-only',
            action='store_true',
            help='Only sync folder structure'
        )
        parser.add_argument(
            '--full',
            action='store_true',
            help='Perform full sync (not incremental)'
        )

    def handle(self, *args, **options):
        from mail.models import MailAccount, MailFolder, MailThread, MailMessage
        from mail.services import StalwartClient

        # Get accounts to sync
        accounts = MailAccount.objects.filter(is_active=True)
        if options['account']:
            accounts = accounts.filter(email_address=options['account'])

        if not accounts.exists():
            self.stdout.write(self.style.WARNING('No accounts to sync'))
            return

        for account in accounts:
            self.stdout.write(f'Syncing account: {account.email_address}')

            try:
                client = StalwartClient(account=account)

                # Sync folders
                self._sync_folders(account, client)

                if not options['folders_only']:
                    # Sync messages
                    self._sync_messages(account, client, full_sync=options['full'])

                self.stdout.write(self.style.SUCCESS(f'  ✓ Synced {account.email_address}'))

            except Exception as e:
                self.stdout.write(self.style.ERROR(f'  ✗ Failed: {e}'))

    def _sync_folders(self, account, client):
        """Sync folders from Stalwart."""
        from mail.models import MailFolder

        folders = client.get_folders()
        self.stdout.write(f'  Found {len(folders)} folders')

        role_map = {
            'inbox': 'inbox',
            'drafts': 'drafts',
            'sent': 'sent',
            'trash': 'trash',
            'junk': 'junk',
            'archive': 'archive',
        }

        for folder_data in folders:
            jmap_id = folder_data.get('id')
            name = folder_data.get('name', 'Unknown')
            role = role_map.get(folder_data.get('role'), 'custom')

            folder, created = MailFolder.objects.update_or_create(
                account=account,
                jmap_mailbox_id=jmap_id,
                defaults={
                    'name': name,
                    'role': role,
                    'total_messages': folder_data.get('totalEmails', 0),
                    'unread_messages': folder_data.get('unreadEmails', 0),
                }
            )

            status = 'created' if created else 'updated'
            self.stdout.write(f'    {status}: {name} ({folder_data.get("totalEmails", 0)} messages)')

    def _sync_messages(self, account, client, full_sync=False):
        """Sync messages from Stalwart."""
        from mail.models import MailFolder, MailMessage, MailThread

        folders = MailFolder.objects.filter(account=account)

        for folder in folders:
            self.stdout.write(f'  Syncing folder: {folder.name}')

            # Get messages from Stalwart
            result = client.get_messages(
                mailbox_id=folder.jmap_mailbox_id,
                limit=100 if full_sync else 50
            )

            emails = result.get('emails', {}).get('list', [])
            synced = 0
            skipped = 0

            for email in emails:
                jmap_id = email.get('id')

                # Check if already synced
                if not full_sync and MailMessage.objects.filter(jmap_email_id=jmap_id).exists():
                    skipped += 1
                    continue

                # Get or create thread
                thread = None
                thread_id = email.get('threadId')
                if thread_id:
                    thread, _ = MailThread.objects.get_or_create(
                        account=account,
                        jmap_thread_id=thread_id,
                        defaults={
                            'subject': email.get('subject', ''),
                            'first_message_at': email.get('receivedAt'),
                            'last_message_at': email.get('receivedAt'),
                        }
                    )

                # Create or update message
                keywords = email.get('keywords', {})
                message, created = MailMessage.objects.update_or_create(
                    jmap_email_id=jmap_id,
                    defaults={
                        'account': account,
                        'folder': folder,
                        'thread': thread,
                        'jmap_blob_id': email.get('blobId'),
                        'message_id': email.get('messageId', [''])[0] if email.get('messageId') else '',
                        'subject': email.get('subject', ''),
                        'from_address': email.get('from', [{}])[0] if email.get('from') else {},
                        'to_addresses': email.get('to', []),
                        'cc_addresses': email.get('cc', []),
                        'bcc_addresses': email.get('bcc', []),
                        'reply_to': email.get('replyTo', [{}])[0] if email.get('replyTo') else None,
                        'preview': email.get('preview', '')[:500],
                        'size_bytes': email.get('size', 0),
                        'is_unread': '$seen' not in keywords,
                        'is_flagged': '$flagged' in keywords,
                        'is_answered': '$answered' in keywords,
                        'is_draft': '$draft' in keywords,
                        'has_attachments': email.get('hasAttachment', False),
                        'attachment_count': len(email.get('attachments', [])),
                        'date_sent': email.get('sentAt'),
                        'date_received': email.get('receivedAt'),
                    }
                )
                synced += 1

            # MAIL-163: refresh contacts from message history after ingestion.
            try:
                from ...services.contact_service import sync_contacts_from_messages
                sync_contacts_from_messages(account)
            except Exception as ce:
                self.stderr.write(f'    MAIL-163 contact sync failed: {ce}')

            self.stdout.write(f'    Synced: {synced}, Skipped: {skipped}')
