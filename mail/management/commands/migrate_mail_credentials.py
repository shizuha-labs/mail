"""One-way MAIL-9 credential envelope migration."""
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from mail.models import MailAccount
from mail.services.credential_service import CredentialService, DecryptionError


class Command(BaseCommand):
    help = (
        "Migrate MailAccount IMAP/SMTP credential fields into MAIL-9 encrypted "
        "envelopes. Output is redacted: account IDs only, never credential values."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report accounts that would be migrated without writing changes.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        try:
            # A migration is a security boundary: bind to the key settings at
            # command invocation, never a process-global instance that may have
            # been initialized before a key rotation or settings override.
            credential_service = CredentialService()
        except Exception as exc:
            raise CommandError("Mail credential key is unavailable; migration aborted fail-closed.") from exc

        migrated_accounts = 0
        migrated_fields = 0
        failed_accounts = 0

        queryset = MailAccount.objects.order_by("id")
        self._preflight_all_credentials(queryset, credential_service)

        for account in queryset.iterator():
            updates = {}
            account_failed = False
            for field_name in ("encrypted_incoming_password", "encrypted_outgoing_password"):
                current = getattr(account, field_name)
                if not current:
                    continue
                try:
                    migrated = credential_service.migrate_stored_value(current)
                except DecryptionError:
                    account_failed = True
                    break
                if migrated != bytes(current):
                    updates[field_name] = migrated

            if account_failed:
                failed_accounts += 1
                self.stderr.write(
                    f"failed account {account.id}: account credentials changed or became undecryptable during migration (redacted)"
                )
                continue

            if not updates:
                continue

            if dry_run:
                migrated_accounts += 1
                migrated_fields += len(updates)
                self.stdout.write(f"migrated account {account.id}")
                continue

            original_values = {field_name: getattr(account, field_name) for field_name in updates}
            with transaction.atomic():
                updated = MailAccount.objects.filter(pk=account.pk, **original_values).update(**updates)
            if updated != 1:
                failed_accounts += 1
                self.stderr.write(
                    f"failed account {account.id}: credential changed during migration; rerun after quiescing writers"
                )
                continue
            migrated_accounts += 1
            migrated_fields += len(updates)
            self.stdout.write(f"migrated account {account.id}")

        summary = (
            f"credential migration complete: accounts={migrated_accounts} "
            f"fields={migrated_fields} failed={failed_accounts} dry_run={dry_run}"
        )
        if failed_accounts:
            raise CommandError(summary)
        self.stdout.write(summary)

    def _preflight_all_credentials(self, queryset, credential_service):
        """Validate every non-empty credential before writing any row.

        This is the wrong-key/corruption canary for MAIL-9. A migration run must
        prove all existing encrypted values are decryptable (or clear plaintext
        leftovers) before it starts re-enveloping rows; otherwise a wrong key
        could partially rewrite plaintext fields before later encrypted fields
        fail. Output remains redacted: account IDs and field names only.
        """
        failures = 0
        for account in queryset.iterator():
            for field_name in ("encrypted_incoming_password", "encrypted_outgoing_password"):
                current = getattr(account, field_name)
                if not current:
                    continue
                try:
                    credential_service.migrate_stored_value(current)
                except DecryptionError:
                    failures += 1
                    self.stderr.write(
                        f"failed account {account.id}: {field_name} could not be migrated in preflight (redacted)"
                    )
        if failures:
            raise CommandError(
                f"credential migration preflight failed: fields={failures}; no rows were modified"
            )
