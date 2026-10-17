# MAIL-30: backfill auth_type='oauth2' for OAuth-connected accounts.
#
# MAIL-41 added OAuth token/provider columns and the OAuth creation path now
# writes auth_type='oauth2'. This data migration repairs any rows created while
# those columns existed but auth_type still held the historical default
# ('password').
from django.db import migrations
from django.db.models import Q


def backfill_oauth_auth_type(apps, schema_editor):
    MailAccount = apps.get_model('mail', 'MailAccount')
    oauth_signal = (
        Q(oauth_provider__gt='')
        | Q(encrypted_oauth_access_token__isnull=False)
        | Q(encrypted_oauth_refresh_token__isnull=False)
        | Q(oauth_token_expiry__isnull=False)
    )
    MailAccount.objects.filter(auth_type='password').filter(oauth_signal).update(auth_type='oauth2')


class Migration(migrations.Migration):

    dependencies = [
        ('mail', '0012_mail33_upper_trgm_indexes'),
        ('mail', '0012_mail41_oauth_token_fields'),
    ]

    operations = [
        migrations.RunPython(backfill_oauth_auth_type, migrations.RunPython.noop),
    ]
