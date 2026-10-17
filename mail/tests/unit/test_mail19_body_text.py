"""
Unit tests for MAIL-19: body_text cache + search_vector trigger v2.

Covers:
- _strip_html HTML-to-plain-text conversion
- _cap_body_text 256 KB cap + truncation marker
- body_text in sync-path helpers (via MailMessage creation)
- body_text redaction via MailRedactionFilter
- Trigger freshness: inserting a MailMessage with body_text makes it findable
  by body term via search_vector (requires Postgres; skipped on other backends).
"""
import logging
from unittest.mock import MagicMock, patch

from django.test import TestCase, skipUnlessDBFeature

from mail.redaction import MailRedactionFilter, _SENSITIVE_KEY_RE
from mail.tasks import _cap_body_text, _strip_html, _BODY_TEXT_MAX_BYTES


class StripHtmlTests(TestCase):
    """Tests for the HTML → plain-text helper."""

    def test_plain_text_passthrough(self):
        self.assertEqual(_strip_html('Hello world'), 'Hello world')

    def test_basic_html_stripped(self):
        result = _strip_html('<p>Hello <b>world</b></p>')
        self.assertIn('Hello', result)
        self.assertIn('world', result)
        self.assertNotIn('<', result)

    def test_script_content_removed(self):
        result = _strip_html('<html><head><script>alert("xss")</script></head><body>Visible</body></html>')
        self.assertNotIn('alert', result)
        self.assertIn('Visible', result)

    def test_style_content_removed(self):
        result = _strip_html('<style>.cls { color: red }</style>Body text here')
        self.assertNotIn('.cls', result)
        self.assertIn('Body text here', result)

    def test_empty_string(self):
        self.assertEqual(_strip_html(''), '')

    def test_none_equivalent(self):
        self.assertEqual(_strip_html(None or ''), '')

    def test_block_elements_add_newline(self):
        result = _strip_html('<p>First</p><p>Second</p>')
        self.assertIn('First', result)
        self.assertIn('Second', result)

    def test_html_entities_decoded(self):
        result = _strip_html('&lt;hello&gt; &amp; world')
        self.assertIn('<hello>', result)
        self.assertIn('& world', result)


class CapBodyTextTests(TestCase):
    """Tests for the 256 KB cap helper."""

    def test_short_text_not_truncated(self):
        text = 'Hello, world!'
        result, truncated = _cap_body_text(text)
        self.assertEqual(result, text)
        self.assertFalse(truncated)

    def test_text_at_limit_not_truncated(self):
        text = 'a' * _BODY_TEXT_MAX_BYTES
        result, truncated = _cap_body_text(text)
        self.assertFalse(truncated)
        self.assertEqual(len(result.encode('utf-8')), _BODY_TEXT_MAX_BYTES)

    def test_text_over_limit_truncated(self):
        text = 'b' * (_BODY_TEXT_MAX_BYTES + 1000)
        result, truncated = _cap_body_text(text)
        self.assertTrue(truncated)
        self.assertLessEqual(len(result.encode('utf-8')), _BODY_TEXT_MAX_BYTES)

    def test_empty_string_not_truncated(self):
        result, truncated = _cap_body_text('')
        self.assertEqual(result, '')
        self.assertFalse(truncated)

    def test_unicode_truncation_safe(self):
        # Multi-byte characters — truncation must not split a codepoint.
        text = '日本語テキスト' * 15000
        result, truncated = _cap_body_text(text)
        self.assertTrue(truncated)
        # Ensure the result is valid UTF-8 (decode must not raise).
        result.encode('utf-8')


class BodyTextRedactionTests(TestCase):
    """body_text must be covered by MailRedactionFilter."""

    def test_body_text_key_matches_sensitive_pattern(self):
        self.assertIsNotNone(_SENSITIVE_KEY_RE.search('body_text'))
        self.assertIsNotNone(_SENSITIVE_KEY_RE.search('body-text'))

    def test_redaction_filter_redacts_body_text_in_dict(self):
        from mail.redaction import redact_sensitive
        result = redact_sensitive({'body_text': 'this is a secret email body'})
        self.assertNotEqual(result.get('body_text'), 'this is a secret email body')

    def test_log_record_body_text_redacted(self):
        """MailRedactionFilter must redact body_text on LogRecord extras."""
        record = logging.LogRecord(
            name='test', level=logging.INFO,
            pathname='', lineno=0,
            msg='processing message',
            args=(), exc_info=None,
        )
        record.body_text = 'confidential email body content'
        f = MailRedactionFilter()
        f.filter(record)
        self.assertNotEqual(record.body_text, 'confidential email body content')


@skipUnlessDBFeature('has_select_for_update')
class TriggerFreshnessTests(TestCase):
    """Trigger v2 freshness: inserting a message with body_text makes it
    findable via search_vector (weight D) without a manual reindex.

    Skipped on non-Postgres backends via skipUnlessDBFeature guard.
    """

    def setUp(self):
        from mail.models import MailAccount, MailFolder
        self.account = MailAccount.objects.create(
            user_id=99001,
            email_address='test-mail19@shizuha.com',
            incoming_host='imap.example.com',
            outgoing_host='smtp.example.com',
            incoming_username='test-mail19@shizuha.com',
        )
        self.folder = MailFolder.objects.create(
            account=self.account,
            name='INBOX',
            jmap_mailbox_id='inbox-99001',
        )

    def test_body_text_indexed_by_trigger(self):
        """A newly created MailMessage with body_text must be findable
        by a body-only search term via search_vector."""
        from django.contrib.postgres.search import SearchQuery, SearchRank
        from mail.models import MailMessage

        unique_term = 'xyzzy_mail19_body_unique_term'
        MailMessage.objects.create(
            account=self.account,
            folder=self.folder,
            subject='Totally unrelated subject',
            from_address='sender@example.com',
            body_text=f'This message contains the unique term {unique_term} in its body.',
        )

        found = MailMessage.objects.annotate(
            rank=SearchRank('search_vector', SearchQuery(unique_term, config='simple'))
        ).filter(rank__gt=0)
        self.assertTrue(
            found.exists(),
            f'Expected to find message by body term "{unique_term}" via search_vector weight D',
        )

    def test_body_text_weight_d_below_subject(self):
        """Body term at weight D must rank below a subject hit at weight A."""
        from django.contrib.postgres.search import SearchQuery, SearchRank
        from mail.models import MailMessage

        shared_term = 'ranktest_mail19_shared'
        # subject hit (weight A)
        msg_subject = MailMessage.objects.create(
            account=self.account,
            folder=self.folder,
            subject=f'Title: {shared_term} in subject',
            from_address='a@example.com',
            body_text='No special term here.',
        )
        # body-only hit (weight D)
        msg_body = MailMessage.objects.create(
            account=self.account,
            folder=self.folder,
            subject='Unrelated subject title',
            from_address='b@example.com',
            body_text=f'Body mentions {shared_term}.',
        )

        q = SearchQuery(shared_term, config='simple')
        msgs = (
            MailMessage.objects
            .annotate(rank=SearchRank('search_vector', q))
            .filter(rank__gt=0)
            .order_by('-rank')
        )
        ranked = list(msgs)
        self.assertGreaterEqual(len(ranked), 2, 'Both messages should be found')
        # Subject hit must come first (higher rank than body-only hit).
        ids = [m.id for m in ranked]
        self.assertLess(ids.index(msg_subject.id), ids.index(msg_body.id),
                        'Subject (weight A) must rank above body-only (weight D)')
