# MAIL-41: add OAuth provider + encrypted token fields to MailAccount so that
# the OAuth account creation flow can store access/refresh tokens and the
# needs_reauth classifier has a creation path that sets auth_type='oauth2'.
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('mail', '0011_mail28_auth_type_needs_reauth'),
    ]

    operations = [
        migrations.AddField(
            model_name='mailaccount',
            name='oauth_provider',
            field=models.CharField(
                blank=True,
                choices=[('gmail', 'Gmail'), ('outlook', 'Outlook / Microsoft 365')],
                default='',
                help_text='OAuth provider name (gmail or outlook); empty for password accounts',
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name='mailaccount',
            name='encrypted_oauth_access_token',
            field=models.BinaryField(
                blank=True,
                null=True,
                help_text='Encrypted OAuth access token for XOAUTH2 IMAP/SMTP auth',
            ),
        ),
        migrations.AddField(
            model_name='mailaccount',
            name='encrypted_oauth_refresh_token',
            field=models.BinaryField(
                blank=True,
                null=True,
                help_text='Encrypted OAuth refresh token for token renewal',
            ),
        ),
        migrations.AddField(
            model_name='mailaccount',
            name='oauth_token_expiry',
            field=models.DateTimeField(
                blank=True,
                null=True,
                help_text='UTC expiry of the current OAuth access token',
            ),
        ),
    ]
