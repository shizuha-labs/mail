"""
Unit tests for MAIL-34: periodic_sync_all_accounts Celery task.

Covers:
- Fan-out: sync_mailbox_counts + sync_new_messages dispatched per account
- Status filter: non-connectable statuses (PERSISTENT + needs_attention) skipped
- 60s dedup guard: recently-synced accounts skipped
- Empty set: no dispatch when all accounts filtered out
- DB error: logs error, returns dispatched=0, does not raise
"""
from datetime import timedelta
from unittest.mock import MagicMock, call, patch

from django.test import TestCase, override_settings
from django.utils import timezone

from mail.tasks import periodic_sync_all_accounts, _NON_CONNECTABLE_STATUSES


def _make_account(pk, status='connected', last_sync_at=None):
    a = MagicMock()
    a.id = pk
    a.user_id = 100 + pk
    a.connection_status = status
    a.last_sync_at = last_sync_at
    return a


class PeriodicSyncDispatchTests(TestCase):

    @patch('mail.tasks.sync_new_messages')
    @patch('mail.tasks.sync_mailbox_counts')
    @patch('mail.models.MailAccount')
    def test_dispatches_per_account(self, MockAccount, mock_sync_counts, mock_sync_new):
        """Two connectable accounts → 2×sync_mailbox_counts + 2×sync_new_messages."""
        accs = [_make_account(1), _make_account(2)]
        MockAccount.objects.exclude.return_value.exclude.return_value.exclude.return_value.only.return_value = accs

        result = periodic_sync_all_accounts.apply().result

        self.assertEqual(result['dispatched'], 2)
        self.assertEqual(mock_sync_counts.delay.call_count, 2)
        self.assertEqual(mock_sync_new.delay.call_count, 2)
        mock_sync_counts.delay.assert_any_call(str(accs[0].id), expected_user_id=accs[0].user_id)
        mock_sync_counts.delay.assert_any_call(str(accs[1].id), expected_user_id=accs[1].user_id)

    @patch('mail.tasks.sync_new_messages')
    @patch('mail.tasks.sync_mailbox_counts')
    @patch('mail.models.MailAccount')
    def test_skips_non_connectable_statuses(self, MockAccount, mock_sync_counts, mock_sync_new):
        """Accounts with non-connectable statuses are excluded by the ORM query."""
        MockAccount.objects.exclude.return_value.exclude.return_value.exclude.return_value.only.return_value = []

        result = periodic_sync_all_accounts.apply().result

        self.assertEqual(result['dispatched'], 0)
        mock_sync_counts.delay.assert_not_called()
        mock_sync_new.delay.assert_not_called()
        # Managed accounts are excluded before status filtering.
        self.assertEqual(
            MockAccount.objects.exclude.call_args.kwargs,
            {'settings__managed_identity': True},
        )
        exclude_call = MockAccount.objects.exclude.return_value.exclude.call_args
        self.assertIn('connection_status__in', exclude_call.kwargs)
        self.assertEqual(exclude_call.kwargs['connection_status__in'], _NON_CONNECTABLE_STATUSES)

    @patch('mail.tasks.sync_new_messages')
    @patch('mail.tasks.sync_mailbox_counts')
    @patch('mail.models.MailAccount')
    def test_dedup_guard_excludes_recent(self, MockAccount, mock_sync_counts, mock_sync_new):
        """last_sync_at__gte cutoff is applied in the second .exclude() call."""
        MockAccount.objects.exclude.return_value.exclude.return_value.exclude.return_value.only.return_value = []

        periodic_sync_all_accounts.apply()

        recent_exclude = MockAccount.objects.exclude.return_value.exclude.return_value.exclude.call_args
        self.assertIn('last_sync_at__gte', recent_exclude.kwargs)

    @patch('mail.tasks.sync_new_messages')
    @patch('mail.tasks.sync_mailbox_counts')
    @patch('mail.models.MailAccount')
    def test_empty_accounts_no_dispatch(self, MockAccount, mock_sync_counts, mock_sync_new):
        """When the filtered set is empty, nothing is dispatched and return is 0."""
        MockAccount.objects.exclude.return_value.exclude.return_value.exclude.return_value.only.return_value = []

        result = periodic_sync_all_accounts.apply().result

        self.assertEqual(result['dispatched'], 0)
        mock_sync_counts.delay.assert_not_called()

    @patch('mail.tasks.sync_new_messages')
    @patch('mail.tasks.sync_mailbox_counts')
    @patch('mail.models.MailAccount')
    def test_db_error_logged_not_raised(self, MockAccount, mock_sync_counts, mock_sync_new):
        """A DB exception is caught, logged as ERROR, and returns dispatched=0."""
        MockAccount.objects.exclude.side_effect = Exception('DB connection refused')

        result = periodic_sync_all_accounts.apply().result

        self.assertEqual(result['dispatched'], 0)
        self.assertIn('error', result)
        mock_sync_counts.delay.assert_not_called()


class NonConnectableStatusesTest(TestCase):

    def test_contains_expected_statuses(self):
        """_NON_CONNECTABLE_STATUSES must include all PERSISTENT_STATUSES + needs_attention."""
        from mail.sync_utils import PERSISTENT_STATUSES
        self.assertTrue(PERSISTENT_STATUSES.issubset(_NON_CONNECTABLE_STATUSES))
        self.assertIn('needs_attention', _NON_CONNECTABLE_STATUSES)
