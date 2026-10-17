import hashlib
import hmac
import json
import time
import uuid
from unittest import mock

from django.core.management import call_command
from django.test import TestCase, override_settings

from mail.models import MailAccount, UserLifecycleEventReceipt
from mail.services.stalwart_client import StalwartManagementClient


@override_settings(
    ID_TO_MAIL_LIFECYCLE_SECRET='test-lifecycle-secret',
    MAIL_DOMAIN='shizuha.com',
    STALWART_JMAP_URL='http://stalwart-shadow.mail-shadow.svc.cluster.local:8080',
    UNIFIED_MAIL_JMAP_URL='http://unified-shadow.mail-shadow.svc.cluster.local:8080',
)
class LifecycleConsumerTests(TestCase):
    def _post(self, payload, *, secret='test-lifecycle-secret'):
        body = json.dumps(payload, separators=(',', ':'), sort_keys=True).encode()
        timestamp = str(int(time.time()))
        digest = hmac.new(
            secret.encode(), timestamp.encode() + b'.' + body, hashlib.sha256,
        ).hexdigest()
        return self.client.post(
            '/api/internal/user-lifecycle-events/',
            data=body,
            content_type='application/json',
            HTTP_X_SHIZUHA_SERVICE='shizuha-id',
            HTTP_X_SHIZUHA_TIMESTAMP=timestamp,
            HTTP_X_SHIZUHA_SIGNATURE=f'sha256={digest}',
        )

    @mock.patch('mail.lifecycle.StalwartManagementClient.ensure_account')
    def test_activation_provisions_once_and_creates_managed_account(self, ensure):
        ensure.return_value = ('account-1', True)
        payload = {
            'event_id': str(uuid.uuid4()),
            'event_type': 'user.activated',
            'user_id': 42,
            'username': 'alice',
            'mailbox_email': 'alice@shizuha.com',
        }

        first = self._post(payload)
        second = self._post(payload)

        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()['status'], 'processed')
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json()['status'], 'deduplicated')
        ensure.assert_called_once_with(
            'alice@shizuha.com', description='Shizuha ID user 42: alice',
        )
        account = MailAccount.objects.get(user_id=42, email_address='alice@shizuha.com')
        self.assertTrue(account.is_active)
        self.assertTrue(account.is_default)
        self.assertTrue(account.settings['managed_identity'])
        self.assertEqual(account.incoming_host, 'unified-shadow.mail-shadow.svc.cluster.local')
        self.assertEqual(account.incoming_port, 8080)
        receipt = UserLifecycleEventReceipt.objects.get(event_id=payload['event_id'])
        self.assertEqual(receipt.status, UserLifecycleEventReceipt.STATUS_PROCESSED)

    @mock.patch('mail.lifecycle.StalwartManagementClient.ensure_account')
    def test_agent_managed_account_replaces_external_default(self, ensure):
        ensure.return_value = ('account-agent', True)
        external = MailAccount.objects.create(
            user_id=45,
            email_address='agent-external@example.com',
            incoming_host='imap.example.com',
            outgoing_host='smtp.example.com',
            incoming_username='agent-external@example.com',
            is_default=True,
        )

        response = self._post({
            'event_id': str(uuid.uuid4()),
            'event_type': 'user.activated',
            'user_id': 45,
            'username': 'agent-user',
            'mailbox_email': 'agent-user@shizuha.com',
            'account_type': 'agent',
        })

        self.assertEqual(response.status_code, 200)
        managed = MailAccount.objects.get(
            user_id=45, email_address='agent-user@shizuha.com',
        )
        external.refresh_from_db()
        self.assertTrue(managed.is_default)
        self.assertFalse(external.is_default)
        self.assertEqual(managed.settings['identity_account_type'], 'agent')

    @mock.patch('mail.lifecycle.StalwartManagementClient.ensure_account')
    def test_managed_account_disables_only_legacy_rows_on_managed_domain(self, ensure):
        ensure.return_value = ('account-unified', True)
        legacy = MailAccount.objects.create(
            user_id=47,
            email_address='old-alias@shizuha.com',
            incoming_host='mail.shizuha.com',
            outgoing_host='mail.shizuha.com',
            incoming_username='old-alias@shizuha.com',
            settings={},
            is_active=True,
        )
        external = MailAccount.objects.create(
            user_id=47,
            email_address='linked@gmail.com',
            incoming_host='imap.gmail.com',
            outgoing_host='smtp.gmail.com',
            incoming_username='linked@gmail.com',
            settings={},
            is_active=True,
        )

        response = self._post({
            'event_id': str(uuid.uuid4()),
            'event_type': 'user.activated',
            'user_id': 47,
            'username': 'unified-user',
            'mailbox_email': 'unified-user@shizuha.com',
        })

        self.assertEqual(response.status_code, 200)
        legacy.refresh_from_db()
        external.refresh_from_db()
        self.assertFalse(legacy.is_active)
        self.assertTrue(external.is_active)

    @mock.patch('mail.lifecycle.StalwartManagementClient.ensure_account')
    def test_human_managed_account_replaces_external_default(self, ensure):
        ensure.return_value = ('account-human', True)
        external = MailAccount.objects.create(
            user_id=46,
            email_address='human-external@example.com',
            incoming_host='imap.example.com',
            outgoing_host='smtp.example.com',
            incoming_username='human-external@example.com',
            is_default=True,
        )

        response = self._post({
            'event_id': str(uuid.uuid4()),
            'event_type': 'user.activated',
            'user_id': 46,
            'username': 'human-user',
            'mailbox_email': 'human-user@shizuha.com',
            'account_type': 'human',
        })

        self.assertEqual(response.status_code, 200)
        managed = MailAccount.objects.get(
            user_id=46, email_address='human-user@shizuha.com',
        )
        external.refresh_from_db()
        self.assertTrue(managed.is_default)
        self.assertFalse(external.is_default)
        self.assertEqual(managed.settings['identity_account_type'], 'human')

    @mock.patch('mail.lifecycle.StalwartManagementClient.ensure_account')
    def test_legacy_payload_also_makes_managed_account_default(self, ensure):
        ensure.return_value = ('account-legacy', True)
        external = MailAccount.objects.create(
            user_id=48,
            email_address='legacy-external@example.com',
            incoming_host='imap.example.com',
            outgoing_host='smtp.example.com',
            incoming_username='legacy-external@example.com',
            is_default=True,
        )

        response = self._post({
            'event_id': str(uuid.uuid4()),
            'event_type': 'user.activated',
            'user_id': 48,
            'username': 'legacy-user',
            'mailbox_email': 'legacy-user@shizuha.com',
        })

        self.assertEqual(response.status_code, 200)
        managed = MailAccount.objects.get(
            user_id=48, email_address='legacy-user@shizuha.com',
        )
        external.refresh_from_db()
        self.assertTrue(managed.is_default)
        self.assertFalse(external.is_default)
        self.assertNotIn('identity_account_type', managed.settings)

    @mock.patch('mail.lifecycle.StalwartManagementClient.ensure_account')
    def test_inactive_external_default_does_not_block_managed_default(self, ensure):
        ensure.return_value = ('account-human', True)
        MailAccount.objects.create(
            user_id=47,
            email_address='inactive-external@example.com',
            incoming_host='imap.example.com',
            outgoing_host='smtp.example.com',
            incoming_username='inactive-external@example.com',
            is_active=False,
            is_default=True,
        )

        response = self._post({
            'event_id': str(uuid.uuid4()),
            'event_type': 'user.activated',
            'user_id': 47,
            'username': 'new-default',
            'mailbox_email': 'new-default@shizuha.com',
            'account_type': 'human',
        })

        self.assertEqual(response.status_code, 200)
        self.assertTrue(MailAccount.objects.get(
            user_id=47, email_address='new-default@shizuha.com',
        ).is_default)

    @mock.patch('mail.lifecycle.StalwartManagementClient.delete_account')
    @mock.patch('mail.lifecycle.StalwartManagementClient.ensure_account')
    def test_deactivation_removes_provider_access_and_disables_local_account(self, ensure, delete):
        MailAccount.objects.create(
            user_id=7,
            email_address='bob@shizuha.com',
            incoming_server_type='jmap',
            incoming_host='stalwart',
            incoming_port=8080,
            incoming_security='none',
            incoming_username='bob@shizuha.com',
            outgoing_host='stalwart',
            outgoing_port=587,
            outgoing_security='starttls',
            outgoing_username='bob@shizuha.com',
        )
        response = self._post({
            'event_id': str(uuid.uuid4()),
            'event_type': 'user.deactivated',
            'user_id': 7,
            'username': 'bob',
            'mailbox_email': 'bob@shizuha.com',
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(MailAccount.objects.get(user_id=7).is_active)
        ensure.assert_not_called()
        delete.assert_called_once_with('bob@shizuha.com')

    @mock.patch('mail.lifecycle.StalwartManagementClient.ensure_account')
    def test_failed_activation_accepts_trusted_mailbox_correction(self, ensure):
        ensure.return_value = ('account-1', True)
        event_id = str(uuid.uuid4())
        UserLifecycleEventReceipt.objects.create(
            event_id=event_id,
            event_type='user.activated',
            user_id=43,
            mailbox_email='legacy@example.com@shizuha.com',
            status=UserLifecycleEventReceipt.STATUS_FAILED,
            attempts=1,
            last_error='provider rejected address',
        )
        corrected = self._post({
            'event_id': event_id,
            'event_type': 'user.activated',
            'user_id': 43,
            'username': 'legacy@example.com',
            'mailbox_email': 'legacy@shizuha.com',
        })

        self.assertEqual(corrected.status_code, 200)
        receipt = UserLifecycleEventReceipt.objects.get(event_id=event_id)
        self.assertEqual(receipt.status, UserLifecycleEventReceipt.STATUS_PROCESSED)
        self.assertEqual(receipt.mailbox_email, 'legacy@shizuha.com')
        self.assertEqual(receipt.attempts, 2)
        self.assertTrue(MailAccount.objects.get(
            user_id=43, email_address='legacy@shizuha.com',
        ).is_active)

    @mock.patch('mail.lifecycle.StalwartManagementClient.ensure_account')
    def test_double_at_mailbox_is_rejected_before_provider_call(self, ensure):
        response = self._post({
            'event_id': str(uuid.uuid4()),
            'event_type': 'user.activated',
            'user_id': 44,
            'username': 'legacy@example.com',
            'mailbox_email': 'legacy@example.com@shizuha.com',
        })
        self.assertEqual(response.status_code, 400)
        ensure.assert_not_called()
        self.assertFalse(UserLifecycleEventReceipt.objects.exists())

    def test_bad_signature_is_rejected(self):
        response = self._post({
            'event_id': str(uuid.uuid4()),
            'event_type': 'user.activated',
            'user_id': 1,
            'username': 'mallory',
        }, secret='wrong-secret')
        self.assertEqual(response.status_code, 401)
        self.assertFalse(UserLifecycleEventReceipt.objects.exists())


class StalwartManagementClientTests(TestCase):
    @override_settings(
        STALWART_PROVISIONING_URL='http://stalwart:8080',
        STALWART_PROVISIONING_USER='admin',
        STALWART_PROVISIONING_PASSWORD='secret',
    )
    def test_ensure_account_uses_v016_jmap_schema(self):
        client = StalwartManagementClient()
        responses = [
            {'methodResponses': [['x:Domain/query', {'ids': ['b']}, 'domain-query']]},
            {'methodResponses': [['x:Account/query', {'ids': []}, 'account-query']]},
            {'methodResponses': [[
                'x:Account/set', {'created': {'mailbox': {'id': 'u1'}}}, 'account-create',
            ]]},
        ]
        with mock.patch.object(client, '_request', side_effect=responses) as request:
            account_id, created = client.ensure_account('alice@shizuha.com')
        self.assertEqual((account_id, created), ('u1', True))
        create = request.call_args_list[2].args[0][0]
        self.assertEqual(create[0], 'x:Account/set')
        account = create[1]['create']['mailbox']
        self.assertEqual(account['@type'], 'User')
        self.assertEqual(account['domainId'], 'b')
        self.assertEqual(account['credentials'], {})
        self.assertEqual(account['roles'], {'@type': 'User'})

    @override_settings(
        STALWART_PROVISIONING_URL='http://stalwart:8080',
        STALWART_PROVISIONING_USER='admin',
        STALWART_PROVISIONING_PASSWORD='secret',
    )
    def test_delete_account_uses_v016_jmap_destroy_and_is_idempotent(self):
        client = StalwartManagementClient()
        responses = [
            {'methodResponses': [['x:Domain/query', {'ids': ['b']}, 'domain-query']]},
            {'methodResponses': [['x:Account/query', {'ids': ['u1']}, 'account-query']]},
            {'methodResponses': [['x:Account/set', {'destroyed': ['u1']}, 'account-destroy']]},
        ]
        with mock.patch.object(client, '_request', side_effect=responses) as request:
            self.assertTrue(client.delete_account('alice@shizuha.com'))
        destroy = request.call_args_list[2].args[0][0]
        self.assertEqual(destroy, [
            'x:Account/set', {'destroy': ['u1']}, 'account-destroy',
        ])

        absent = [
            {'methodResponses': [['x:Domain/query', {'ids': ['b']}, 'domain-query']]},
            {'methodResponses': [['x:Account/query', {'ids': []}, 'account-query']]},
        ]
        with mock.patch.object(client, '_request', side_effect=absent):
            self.assertFalse(client.delete_account('alice@shizuha.com'))


@override_settings(
    STALWART_JMAP_URL='http://legacy.example:8580',
    UNIFIED_MAIL_JMAP_URL='http://stalwart-shadow.mail-shadow.svc.cluster.local:8080',
)
class ReconcileManagedMailAccountsTests(TestCase):
    @mock.patch('mail.management.commands.reconcile_managed_mail_accounts.StalwartManagementClient')
    def test_updates_active_routes_and_removes_only_inactive_managed_accounts(self, client_type):
        active = MailAccount.objects.create(
            user_id=501, email_address='active@shizuha.com',
            incoming_host='legacy.example', incoming_port=8580,
            outgoing_host='legacy.example', outgoing_port=25,
            settings={'managed_identity': True}, is_active=True, is_default=False,
        )
        external = MailAccount.objects.create(
            user_id=501, email_address='active-external@example.com',
            incoming_host='imap.example.com', incoming_port=993,
            outgoing_host='smtp.example.com', outgoing_port=465,
            settings={}, is_active=True, is_default=True,
        )
        inactive = MailAccount.objects.create(
            user_id=502, email_address='inactive@shizuha.com',
            incoming_host='legacy.example', incoming_port=8580,
            outgoing_host='legacy.example', outgoing_port=25,
            settings={'managed_identity': True}, is_active=False,
        )
        unmanaged = MailAccount.objects.create(
            user_id=503, email_address='external@example.com',
            incoming_host='imap.example.com', incoming_port=993,
            outgoing_host='smtp.example.com', outgoing_port=465,
            settings={}, is_active=False,
        )
        client_type.return_value.delete_account.return_value = True

        call_command('reconcile_managed_mail_accounts')

        active.refresh_from_db(); external.refresh_from_db()
        inactive.refresh_from_db(); unmanaged.refresh_from_db()
        self.assertEqual(active.incoming_host, 'stalwart-shadow.mail-shadow.svc.cluster.local')
        self.assertEqual(active.incoming_port, 8080)
        self.assertEqual(active.outgoing_host, 'stalwart-shadow-mail.mail-shadow.svc.cluster.local')
        self.assertTrue(active.is_default)
        self.assertFalse(external.is_default)
        client_type.return_value.delete_account.assert_called_once_with('inactive@shizuha.com')
        self.assertEqual(unmanaged.incoming_host, 'imap.example.com')

    @mock.patch('mail.management.commands.reconcile_managed_mail_accounts.StalwartManagementClient')
    def test_reconcile_expires_old_synthetic_fixtures_but_keeps_managed_and_recent(self, client_type):
        # MAIL-107: a completed WIKI-27 synthetic fixture (@shizuha.local) must
        # not remain active in a real profile past its TTL.
        from django.utils import timezone
        from datetime import timedelta

        old_synthetic = MailAccount.objects.create(
            user_id=601, email_address='cvqa27mail050437@shizuha.local',
            incoming_host='imap.example.com', incoming_port=993,
            outgoing_host='smtp.example.com', outgoing_port=465,
            settings={}, is_active=True, is_default=False,
        )
        MailAccount.objects.filter(pk=old_synthetic.pk).update(
            created_at=timezone.now() - timedelta(days=30),
        )
        recent_synthetic = MailAccount.objects.create(
            user_id=602, email_address='fresh-fixture@shizuha.local',
            incoming_host='imap.example.com', incoming_port=993,
            outgoing_host='smtp.example.com', outgoing_port=465,
            settings={}, is_active=True, is_default=False,
        )
        managed = MailAccount.objects.create(
            user_id=603, email_address='zen@shizuha.com',
            incoming_host='legacy.example', incoming_port=8580,
            outgoing_host='legacy.example', outgoing_port=25,
            settings={'managed_identity': True}, is_active=True, is_default=True,
        )
        client_type.return_value.delete_account.return_value = True

        call_command('reconcile_managed_mail_accounts')

        old_synthetic.refresh_from_db(); recent_synthetic.refresh_from_db(); managed.refresh_from_db()
        self.assertFalse(old_synthetic.is_active)
        self.assertFalse(old_synthetic.is_default)
        self.assertTrue(recent_synthetic.is_active)
        self.assertTrue(managed.is_active)
        self.assertTrue(managed.is_default)
