# MAIL-28: add auth_type field + expand CONNECTION_STATUS_CHOICES with
# needs_reauth and needs_attention (both were already emitted by tasks.py
# but absent from the model's choices list).
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('mail', '0010_mail19_body_text_fts_v2'),
        ('mail', '0010_trgm_threading_indexes'),
    ]

    operations = [
        migrations.AddField(
            model_name='mailaccount',
            name='auth_type',
            field=models.CharField(
                choices=[('password', 'Password'), ('oauth2', 'OAuth 2.0')],
                default='password',
                help_text='Authentication mechanism: password or OAuth 2.0 token',
                max_length=10,
            ),
        ),
        migrations.AlterField(
            model_name='mailaccount',
            name='connection_status',
            field=models.CharField(
                choices=[
                    ('unknown', 'Unknown'),
                    ('connected', 'Connected'),
                    ('auth_failed', 'Authentication Failed'),
                    ('needs_reauth', 'Re-authorization Required'),
                    ('connection_failed', 'Connection Failed'),
                    ('ssl_error', 'SSL/TLS Error'),
                    ('needs_attention', 'Needs Attention'),
                ],
                default='unknown',
                help_text='Last known connection status',
                max_length=20,
            ),
        ),
    ]
