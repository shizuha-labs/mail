"""Disable password-era Shizuha-domain rows superseded by managed mail."""

from django.conf import settings
from django.db import migrations
from django.utils import timezone


def disable_superseded_domain_accounts(apps, schema_editor):
    MailAccount = apps.get_model('mail', 'MailAccount')
    managed_accounts = MailAccount.objects.filter(
        is_active=True,
        settings__managed_identity=True,
    ).values_list('id', 'user_id')
    legacy_ids = []
    for managed_id, user_id in managed_accounts.iterator():
        candidates = MailAccount.objects.filter(
            user_id=user_id,
            is_active=True,
            email_address__iendswith=f'@{settings.MAIL_DOMAIN}',
        ).exclude(pk=managed_id).values_list('id', 'settings')
        legacy_ids.extend(
            account_id
            for account_id, account_settings in candidates.iterator()
            if not (account_settings or {}).get('managed_identity')
        )
    MailAccount.objects.filter(pk__in=legacy_ids).update(
        is_active=False,
        is_default=False,
        updated_at=timezone.now(),
    )


class Migration(migrations.Migration):
    dependencies = [
        ('mail', '0019_managed_mail_is_universal_default'),
    ]

    operations = [
        migrations.RunPython(
            disable_superseded_domain_accounts,
            migrations.RunPython.noop,
        ),
    ]
