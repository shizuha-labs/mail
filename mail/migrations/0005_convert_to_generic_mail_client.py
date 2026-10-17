"""
Migration to convert shizuha-mail from auto-provisioning to generic mail client.

This migration:
1. Removes the unique constraint on user_id (allow multiple accounts per user)
2. Removes Stalwart-specific fields (stalwart_account_id, stalwart_password)
3. Adds generic email server configuration fields
4. Adds encrypted credential storage
5. Adds multi-account support fields
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('mail', '0004_add_stalwart_password'),
    ]

    operations = [
        # Step 1: Remove old constraints and fields

        # Remove unique constraint on user_id to allow multiple accounts per user
        migrations.AlterField(
            model_name='mailaccount',
            name='user_id',
            field=models.IntegerField(db_index=True, help_text='User ID from Shizuha ID'),
        ),

        # Remove unique constraint on email_address (will add unique_together later)
        migrations.AlterField(
            model_name='mailaccount',
            name='email_address',
            field=models.EmailField(max_length=254, help_text='Email address for this account'),
        ),

        # Remove Stalwart-specific fields
        migrations.RemoveField(
            model_name='mailaccount',
            name='stalwart_account_id',
        ),
        migrations.RemoveField(
            model_name='mailaccount',
            name='stalwart_password',
        ),

        # Remove user_email field (no longer auto-deriving from Shizuha ID)
        migrations.RemoveField(
            model_name='mailaccount',
            name='user_email',
        ),

        # Remove quota fields (managed by mail server, not us)
        migrations.RemoveField(
            model_name='mailaccount',
            name='storage_quota_mb',
        ),
        migrations.RemoveField(
            model_name='mailaccount',
            name='storage_used_mb',
        ),

        # Remove is_verified (we test connection instead)
        migrations.RemoveField(
            model_name='mailaccount',
            name='is_verified',
        ),

        # Step 2: Add new fields for generic mail client

        # Account display settings
        migrations.AddField(
            model_name='mailaccount',
            name='display_name',
            field=models.CharField(blank=True, help_text="Account label (e.g., 'Work', 'Personal')", max_length=100),
        ),
        migrations.AddField(
            model_name='mailaccount',
            name='from_name',
            field=models.CharField(blank=True, help_text='Display name for From header', max_length=255),
        ),

        # Incoming server configuration
        migrations.AddField(
            model_name='mailaccount',
            name='incoming_server_type',
            field=models.CharField(
                choices=[('imap', 'IMAP'), ('jmap', 'JMAP')],
                default='imap',
                help_text='Protocol for receiving mail',
                max_length=10
            ),
        ),
        migrations.AddField(
            model_name='mailaccount',
            name='incoming_host',
            field=models.CharField(default='', help_text='Incoming mail server hostname', max_length=255),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='mailaccount',
            name='incoming_port',
            field=models.IntegerField(default=993, help_text='Incoming mail server port'),
        ),
        migrations.AddField(
            model_name='mailaccount',
            name='incoming_security',
            field=models.CharField(
                choices=[('ssl', 'SSL/TLS'), ('starttls', 'STARTTLS'), ('none', 'None')],
                default='ssl',
                help_text='Connection security for incoming',
                max_length=10
            ),
        ),
        migrations.AddField(
            model_name='mailaccount',
            name='incoming_username',
            field=models.CharField(default='', help_text='Username for incoming server (usually email)', max_length=255),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='mailaccount',
            name='encrypted_incoming_password',
            field=models.BinaryField(blank=True, help_text='Encrypted password for incoming server', null=True),
        ),

        # Outgoing server configuration
        migrations.AddField(
            model_name='mailaccount',
            name='outgoing_host',
            field=models.CharField(default='', help_text='SMTP server hostname', max_length=255),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='mailaccount',
            name='outgoing_port',
            field=models.IntegerField(default=587, help_text='SMTP server port'),
        ),
        migrations.AddField(
            model_name='mailaccount',
            name='outgoing_security',
            field=models.CharField(
                choices=[('ssl', 'SSL/TLS'), ('starttls', 'STARTTLS'), ('none', 'None')],
                default='starttls',
                help_text='Connection security for outgoing',
                max_length=10
            ),
        ),
        migrations.AddField(
            model_name='mailaccount',
            name='outgoing_username',
            field=models.CharField(blank=True, help_text='Username for SMTP (defaults to incoming)', max_length=255),
        ),
        migrations.AddField(
            model_name='mailaccount',
            name='encrypted_outgoing_password',
            field=models.BinaryField(blank=True, help_text='Encrypted password for SMTP (if different from incoming)', null=True),
        ),
        migrations.AddField(
            model_name='mailaccount',
            name='outgoing_requires_auth',
            field=models.BooleanField(default=True, help_text='Whether SMTP requires authentication'),
        ),

        # Multi-account and status fields
        migrations.AddField(
            model_name='mailaccount',
            name='is_default',
            field=models.BooleanField(default=False, help_text='Default account for sending'),
        ),
        migrations.AddField(
            model_name='mailaccount',
            name='connection_status',
            field=models.CharField(
                choices=[
                    ('unknown', 'Unknown'),
                    ('connected', 'Connected'),
                    ('auth_failed', 'Authentication Failed'),
                    ('connection_failed', 'Connection Failed'),
                    ('ssl_error', 'SSL/TLS Error'),
                ],
                default='unknown',
                help_text='Last known connection status',
                max_length=20
            ),
        ),
        migrations.AddField(
            model_name='mailaccount',
            name='connection_error',
            field=models.TextField(blank=True, help_text='Last connection error message'),
        ),
        migrations.AddField(
            model_name='mailaccount',
            name='last_connection_check',
            field=models.DateTimeField(blank=True, null=True),
        ),

        # Step 3: Update constraints

        # Add unique together constraint for user_id + email_address
        migrations.AlterUniqueTogether(
            name='mailaccount',
            unique_together={('user_id', 'email_address')},
        ),

        # Update ordering
        migrations.AlterModelOptions(
            name='mailaccount',
            options={'ordering': ['-is_default', '-created_at']},
        ),
    ]
