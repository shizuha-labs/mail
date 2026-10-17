"""MAIL-30: OAuth-connected accounts are backfilled to auth_type='oauth2'."""

import importlib

from django.test import TestCase
from django.utils import timezone

from mail.models import MailAccount


mail30_migration = importlib.import_module('mail.migrations.0013_mail30_oauth_auth_type_backfill')


class _Apps:
    @staticmethod
    def get_model(app_label, model_name):
        assert (app_label, model_name) == ('mail', 'MailAccount')
        return MailAccount


def _account(email, **kwargs):
    defaults = dict(
        user_id=1,
        email_address=email,
        incoming_host='imap.example.com',
        outgoing_host='smtp.example.com',
        incoming_username=email,
    )
    defaults.update(kwargs)
    return MailAccount.objects.create(**defaults)


class OAuthAuthTypeBackfillTests(TestCase):
    def test_backfills_password_default_when_oauth_provider_is_set(self):
        account = _account(
            'oauth-provider@example.com',
            auth_type='password',
            oauth_provider='gmail',
        )

        mail30_migration.backfill_oauth_auth_type(_Apps(), None)

        account.refresh_from_db()
        self.assertEqual(account.auth_type, 'oauth2')

    def test_backfills_password_default_when_oauth_tokens_exist(self):
        account = _account(
            'oauth-token@example.com',
            auth_type='password',
            encrypted_oauth_access_token=b'encrypted-access-token',
            encrypted_oauth_refresh_token=b'encrypted-refresh-token',
            oauth_token_expiry=timezone.now(),
        )

        mail30_migration.backfill_oauth_auth_type(_Apps(), None)

        account.refresh_from_db()
        self.assertEqual(account.auth_type, 'oauth2')

    def test_leaves_plain_password_accounts_unchanged(self):
        account = _account('password@example.com', auth_type='password')

        mail30_migration.backfill_oauth_auth_type(_Apps(), None)

        account.refresh_from_db()
        self.assertEqual(account.auth_type, 'password')

    def test_leaves_existing_oauth_accounts_unchanged(self):
        account = _account(
            'already-oauth@example.com',
            auth_type='oauth2',
            oauth_provider='outlook',
        )

        mail30_migration.backfill_oauth_auth_type(_Apps(), None)

        account.refresh_from_db()
        self.assertEqual(account.auth_type, 'oauth2')
