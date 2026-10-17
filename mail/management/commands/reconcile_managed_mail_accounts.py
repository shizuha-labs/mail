"""Converge unified ID-backed MailAccount routing and inactive providers."""

from django.core.management.base import BaseCommand

from mail.lifecycle import (
    disable_superseded_domain_accounts,
    disable_synthetic_fixture_accounts,
    unified_account_routing,
)
from mail.models import MailAccount
from mail.services.stalwart_client import StalwartManagementClient


class Command(BaseCommand):
    help = (
        'Point active managed accounts at the unified cluster mail plane, '
        'remove provider accounts for identities already marked inactive, and '
        'expire synthetic QA-fixture accounts past their TTL (MAIL-107).'
    )

    def handle(self, *args, **options):
        managed = MailAccount.objects.filter(settings__managed_identity=True)
        routing = unified_account_routing()
        updated = managed.filter(is_active=True).update(**routing)
        defaults_updated = 0
        for account in managed.filter(is_active=True).iterator():
            if not account.is_default:
                account.is_default = True
                account.save(update_fields=['is_default', 'updated_at'])
                defaults_updated += 1

        legacy_domain_accounts_disabled = sum(
            disable_superseded_domain_accounts(
                user_id=account.user_id,
                keep_account_id=account.pk,
            )
            for account in managed.filter(is_active=True).iterator()
        )

        synthetic_fixtures_expired = disable_synthetic_fixture_accounts()

        client = StalwartManagementClient()
        removed = already_absent = 0
        for email in managed.filter(is_active=False).values_list('email_address', flat=True):
            if client.delete_account(email):
                removed += 1
            else:
                already_absent += 1

        self.stdout.write(
            f'active_routes_updated={updated} managed_defaults_updated={defaults_updated} '
            f'legacy_domain_accounts_disabled={legacy_domain_accounts_disabled} '
            f'synthetic_fixtures_expired={synthetic_fixtures_expired} '
            f'inactive_provider_removed={removed} '
            f'inactive_provider_already_absent={already_absent}'
        )
