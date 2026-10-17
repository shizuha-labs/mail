"""Make managed Shizuha Mail the default account for every active identity."""

from django.db import migrations


def make_managed_mail_default(apps, schema_editor):
    MailAccount = apps.get_model('mail', 'MailAccount')
    managed = MailAccount.objects.filter(
        is_active=True,
        settings__managed_identity=True,
    )
    for account in managed.iterator():
        MailAccount.objects.filter(
            user_id=account.user_id,
            is_default=True,
        ).exclude(pk=account.pk).update(is_default=False)
        if not account.is_default:
            MailAccount.objects.filter(pk=account.pk).update(is_default=True)


class Migration(migrations.Migration):
    dependencies = [
        ('mail', '0018_outbox_provider_started'),
    ]

    operations = [
        migrations.RunPython(
            make_managed_mail_default,
            migrations.RunPython.noop,
        ),
    ]
