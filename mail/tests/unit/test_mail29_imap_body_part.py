"""MAIL-29: IMAP FTS body_text should index only text/plain first part."""

from django.test import SimpleTestCase

from mail.services.imap_client import IMAPClient


def _header_tuple(uid: int, content_type: str, subject: str = 'Test message'):
    headers = (
        'From: Sender <sender@example.com>\r\n'
        'To: Recipient <recipient@example.com>\r\n'
        f'Subject: {subject}\r\n'
        'Date: Mon, 01 Jan 2024 00:00:00 +0000\r\n'
        f'Content-Type: {content_type}\r\n'
        '\r\n'
    ).encode()
    return (
        f'1 (UID {uid} FLAGS () RFC822.SIZE 123 BODY[HEADER.FIELDS (...)])'.encode(),
        headers,
    )


class FakeIMAPConnection:
    def __init__(self, *, header_content_type: str = 'text/plain', body_data, header_data=None, search_uids=None):
        self.header_content_type = header_content_type
        self.body_data = body_data
        self.header_data = header_data
        self.search_uids = search_uids or [b'42']
        self.fetch_requests = []

    def select(self, folder_name, readonly=False):
        return 'OK', [b'1']

    def response(self, name):
        return 'OK', [b'12345']

    def uid(self, command, *args):
        if command == 'SEARCH':
            return 'OK', [b' '.join(self.search_uids)]
        if command == 'FETCH':
            uid_str, fetch_items = args
            self.fetch_requests.append((uid_str, fetch_items))
            if 'HEADER.FIELDS' in fetch_items:
                if self.header_data is not None:
                    return 'OK', self.header_data
                return 'OK', [_header_tuple(42, self.header_content_type)]
            return 'OK', self.body_data
        raise AssertionError(f'unexpected uid call: {command} {args!r}')


def _client_with(fake_connection):
    client = IMAPClient('imap.example.com')
    client._connection = fake_connection
    client._connected = True
    return client


class Mail29IMAPBodyPartTests(SimpleTestCase):
    def test_multipart_alternative_indexes_plain_first_part_without_mime_boundaries(self):
        fake = FakeIMAPConnection(
            header_content_type='multipart/alternative; boundary="abc"',
            body_data=[
                (b'1 (UID 42 BODY[1.MIME] {43}', b'Content-Type: text/plain; charset=utf-8\r\n\r\n'),
                (b' BODY[1] {22}', b'Plain body only\r\nLine 2'),
                b')',
            ],
        )

        [msg] = _client_with(fake).fetch_messages('INBOX')

        self.assertEqual(msg.body_text, 'Plain body only\r\nLine 2')
        self.assertNotIn('boundary="abc"', msg.body_text)
        self.assertIn('BODY.PEEK[1]', fake.fetch_requests[-1][1])
        self.assertNotIn('BODY.PEEK[TEXT]', fake.fetch_requests[-1][1])

    def test_non_text_first_part_is_not_indexed(self):
        fake = FakeIMAPConnection(
            header_content_type='multipart/alternative; boundary="abc"',
            body_data=[
                (b'1 (UID 42 BODY[1.MIME] {39}', b'Content-Type: text/html; charset=utf-8\r\n\r\n'),
                (b' BODY[1] {12}', b'<p>HTML</p>'),
                b')',
            ],
        )

        [msg] = _client_with(fake).fetch_messages('INBOX')

        self.assertIsNone(msg.body_text)

    def test_single_part_plain_text_remains_indexed_when_part_mime_is_absent(self):
        fake = FakeIMAPConnection(
            header_content_type='text/plain; charset=utf-8',
            body_data=[
                (b'1 (UID 42 BODY[1] {10}', b'Plain body'),
                b')',
            ],
        )

        [msg] = _client_with(fake).fetch_messages('INBOX')

        self.assertEqual(msg.body_text, 'Plain body')

    def test_multipart_without_part_mime_is_not_indexed(self):
        fake = FakeIMAPConnection(
            header_content_type='multipart/mixed; boundary="abc"',
            body_data=[
                (b'1 (UID 42 BODY[1] {10}', b'could be attachment bytes'),
                b')',
            ],
        )

        [msg] = _client_with(fake).fetch_messages('INBOX')

        self.assertIsNone(msg.body_text)

    def test_nil_first_part_literal_is_not_indexed_as_preview_text(self):
        fake = FakeIMAPConnection(
            header_content_type='text/plain; charset=utf-8',
            body_data=[
                (b'1 (UID 42 BODY[1] {3}', b'NIL'),
                b')',
            ],
        )

        [msg] = _client_with(fake).fetch_messages('INBOX')

        self.assertIsNone(msg.body_text)

    def test_unknown_uid_fetch_resets_last_uid_before_continuation(self):
        fake = FakeIMAPConnection(
            header_content_type='text/plain; charset=utf-8',
            body_data=[
                (b'1 (UID 999 BODY[1.MIME] {43}', b'Content-Type: text/plain; charset=utf-8\r\n\r\n'),
                (b' BODY[1] {21}', b'should not be stored'),
                (b'2 (UID 42 BODY[1.MIME] {43}', b'Content-Type: text/plain; charset=utf-8\r\n\r\n'),
                (b' BODY[1] {21}', b'valid message body'),
                b')',
            ],
        )

        [msg] = _client_with(fake).fetch_messages('INBOX')

        self.assertEqual(msg.body_text, 'valid message body')

    def test_multi_message_batch_keeps_body_text_attributed_to_each_uid(self):
        fake = FakeIMAPConnection(
            search_uids=[b'42', b'43'],
            header_data=[
                _header_tuple(43, 'multipart/alternative; boundary="abc"', subject='Second'),
                _header_tuple(42, 'multipart/alternative; boundary="abc"', subject='First'),
            ],
            body_data=[
                (b'1 (UID 43 BODY[1.MIME] {43}', b'Content-Type: text/plain; charset=utf-8\r\n\r\n'),
                (b' BODY[1] {12}', b'Body for 43'),
                b')',
                (b'2 (UID 42 BODY[1.MIME] {43}', b'Content-Type: text/plain; charset=utf-8\r\n\r\n'),
                (b' BODY[1] {12}', b'Body for 42'),
                b')',
            ],
        )

        messages = _client_with(fake).fetch_messages('INBOX')
        uid_to_text = {msg.uid: msg.body_text for msg in messages}

        self.assertEqual(uid_to_text['42'], 'Body for 42')
        self.assertEqual(uid_to_text['43'], 'Body for 43')
