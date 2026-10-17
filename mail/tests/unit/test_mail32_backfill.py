"""
Unit tests for MAIL-32: backfill_message_body_text Celery task.

Covers:
- Rows with body_text=None are processed; already-populated rows are skipped (idempotent)
- 256 KB cap enforced (body_text_truncated set on truncated rows)
- Per-row errors logged and skipped, not propagated
- Only body_text IS NULL rows are queried (second run processes nothing)
- JMAP and IMAP dispatch paths
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch, call

from django.test import TestCase, override_settings

from mail.tasks import backfill_message_body_text, _extract_jmap_body, _BODY_TEXT_MAX_BYTES


# ── _extract_jmap_body ────────────────────────────────────────────────────────

class ExtractJmapBodyTests(TestCase):

    def test_extracts_from_textBody(self):
        email_data = {
            'textBody': [{'partId': 'p1'}],
            'bodyValues': {'p1': {'value': 'Hello world'}},
        }
        self.assertEqual(_extract_jmap_body(email_data), 'Hello world')

    def test_falls_back_to_all_bodyValues(self):
        email_data = {
            'textBody': [],
            'bodyValues': {'p1': {'value': 'fallback text'}},
        }
        self.assertEqual(_extract_jmap_body(email_data), 'fallback text')

    def test_empty_data(self):
        self.assertEqual(_extract_jmap_body({}), '')

    def test_concatenates_multiple_parts(self):
        email_data = {
            'textBody': [{'partId': 'p1'}, {'partId': 'p2'}],
            'bodyValues': {'p1': {'value': 'Part one. '}, 'p2': {'value': 'Part two.'}},
        }
        result = _extract_jmap_body(email_data)
        self.assertIn('Part one', result)
        self.assertIn('Part two', result)


# ── backfill_message_body_text ────────────────────────────────────────────────

def _make_jmap_message(pk, jmap_email_id='jmap-001', body_text=None):
    """Build a minimal mock MailMessage (JMAP)."""
    msg = MagicMock()
    msg.pk = pk
    msg.account_id = 42
    msg.jmap_email_id = jmap_email_id
    msg.imap_uid = ''
    msg.folder = None
    msg.body_text = body_text
    msg.account.incoming_server_type = 'jmap'
    return msg


def _make_imap_message(pk, imap_uid='100', folder_name='INBOX', body_text=None):
    """Build a minimal mock MailMessage (IMAP)."""
    msg = MagicMock()
    msg.pk = pk
    msg.account_id = 43
    msg.jmap_email_id = ''
    msg.imap_uid = imap_uid
    msg.folder.name = folder_name
    msg.body_text = body_text
    msg.account.incoming_server_type = 'imap'
    return msg


class BackfillTaskTests(TestCase):

    def _run_task(self):
        """Apply the task synchronously (eager mode)."""
        return backfill_message_body_text.apply().result

    @patch('mail.models.MailMessage', autospec=False)
    @patch('mail.tasks._get_stalwart_client')
    def test_processes_jmap_null_rows(self, mock_get_stalwart, MockMailMessage):
        """JMAP messages with body_text=None are fetched and updated."""
        msg = _make_jmap_message(pk=1)
        # Simulate one batch then empty
        MockMailMessage.objects.filter.return_value.count.return_value = 1
        MockMailMessage.objects.filter.return_value.select_related.return_value \
            .order_by.return_value.__getitem__.side_effect = [[msg], []]

        mock_client = MagicMock()
        mock_client.get_email = AsyncMock(return_value={
            'textBody': [{'partId': 'p1'}],
            'bodyValues': {'p1': {'value': 'Hello from JMAP'}},
        })
        mock_get_stalwart.return_value = mock_client

        result = self._run_task()

        self.assertEqual(result['processed'], 1)
        self.assertEqual(result['errors'], 0)
        MockMailMessage.objects.filter.return_value.update.assert_called_once()
        update_kwargs = MockMailMessage.objects.filter.return_value.update.call_args[1]
        self.assertIn('Hello from JMAP', update_kwargs.get('body_text', ''))
        self.assertFalse(update_kwargs.get('body_text_truncated', True))

    @patch('mail.models.MailMessage', autospec=False)
    @patch('mail.tasks._get_stalwart_client')
    def test_skips_already_populated(self, mock_get_stalwart, MockMailMessage):
        """Rows with body_text already set are never fetched (idempotent)."""
        MockMailMessage.objects.filter.return_value.count.return_value = 0
        MockMailMessage.objects.filter.return_value.select_related.return_value \
            .order_by.return_value.__getitem__.return_value = []

        result = self._run_task()

        self.assertEqual(result['processed'], 0)
        mock_get_stalwart.assert_not_called()

    @patch('mail.models.MailMessage', autospec=False)
    @patch('mail.tasks._get_stalwart_client')
    def test_enforces_256kb_cap(self, mock_get_stalwart, MockMailMessage):
        """Body text exceeding 256 KB is truncated and body_text_truncated=True."""
        msg = _make_jmap_message(pk=2)
        MockMailMessage.objects.filter.return_value.count.return_value = 1
        MockMailMessage.objects.filter.return_value.select_related.return_value \
            .order_by.return_value.__getitem__.side_effect = [[msg], []]

        large_body = 'x' * (_BODY_TEXT_MAX_BYTES + 1000)
        mock_client = MagicMock()
        mock_client.get_email = AsyncMock(return_value={
            'textBody': [{'partId': 'p1'}],
            'bodyValues': {'p1': {'value': large_body}},
        })
        mock_get_stalwart.return_value = mock_client

        result = self._run_task()

        self.assertEqual(result['processed'], 1)
        update_kwargs = MockMailMessage.objects.filter.return_value.update.call_args[1]
        self.assertTrue(update_kwargs.get('body_text_truncated'))
        stored = update_kwargs.get('body_text', '')
        self.assertLessEqual(len(stored.encode('utf-8')), _BODY_TEXT_MAX_BYTES)

    @patch('mail.models.MailMessage', autospec=False)
    @patch('mail.tasks._get_stalwart_client')
    def test_per_row_error_skipped(self, mock_get_stalwart, MockMailMessage):
        """A per-row fetch error is logged and skipped; other rows still process."""
        msg1 = _make_jmap_message(pk=3, jmap_email_id='bad-id')
        msg2 = _make_jmap_message(pk=4, jmap_email_id='good-id')
        MockMailMessage.objects.filter.return_value.count.return_value = 2
        MockMailMessage.objects.filter.return_value.select_related.return_value \
            .order_by.return_value.__getitem__.side_effect = [[msg1, msg2], []]

        mock_client = MagicMock()
        mock_client.get_email = AsyncMock(side_effect=[
            Exception('Stalwart unavailable'),
            {'textBody': [{'partId': 'p1'}], 'bodyValues': {'p1': {'value': 'good body'}}},
        ])
        mock_get_stalwart.return_value = mock_client

        result = self._run_task()

        self.assertEqual(result['errors'], 1)
        self.assertEqual(result['processed'], 1)

    @patch('mail.models.MailMessage', autospec=False)
    @patch('mail.tasks._get_imap_client')
    def test_imap_dispatch(self, mock_get_imap, MockMailMessage):
        """IMAP messages are fetched via imap_client.fetch_message."""
        msg = _make_imap_message(pk=5)
        MockMailMessage.objects.filter.return_value.count.return_value = 1
        MockMailMessage.objects.filter.return_value.select_related.return_value \
            .order_by.return_value.__getitem__.side_effect = [[msg], []]

        mock_imap = MagicMock()
        mock_imap.__enter__ = MagicMock(return_value=mock_imap)
        mock_imap.__exit__ = MagicMock(return_value=False)
        mock_imap.fetch_message.return_value = {'body_text': 'Plain IMAP text', 'body_html': ''}
        mock_get_imap.return_value = mock_imap

        result = self._run_task()

        self.assertEqual(result['processed'], 1)
        mock_imap.fetch_message.assert_called_once_with('INBOX', '100', include_body=True)

    @override_settings(MAIL_BACKFILL_BATCH_SIZE=2)
    @patch('mail.models.MailMessage', autospec=False)
    @patch('mail.tasks._get_stalwart_client')
    @patch('time.sleep')
    def test_batch_size_respected(self, mock_sleep, mock_get_stalwart, MockMailMessage):
        """Task sleeps between batches and processes all rows across multiple batches."""
        msgs_b1 = [_make_jmap_message(pk=i) for i in range(1, 3)]
        msgs_b2 = [_make_jmap_message(pk=i) for i in range(3, 5)]
        MockMailMessage.objects.filter.return_value.count.return_value = 4
        MockMailMessage.objects.filter.return_value.select_related.return_value \
            .order_by.return_value.__getitem__.side_effect = [msgs_b1, msgs_b2, []]

        mock_client = MagicMock()
        mock_client.get_email = AsyncMock(return_value={
            'textBody': [{'partId': 'p1'}],
            'bodyValues': {'p1': {'value': 'some text'}},
        })
        mock_get_stalwart.return_value = mock_client

        result = self._run_task()

        self.assertEqual(result['processed'], 4)
        self.assertEqual(mock_sleep.call_count, 2)
