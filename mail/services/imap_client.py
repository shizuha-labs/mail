"""
IMAP Client for generic email server access.

Supports connecting to any IMAP server with SSL/TLS/STARTTLS.
Provides folder listing, message fetching, and flag management.
"""
import email
import imaplib
import logging
import re
import ssl
from dataclasses import dataclass
from datetime import datetime
from email.header import decode_header
from typing import Any, Optional

logger = logging.getLogger(__name__)


class IMAPError(Exception):
    """Base exception for IMAP errors."""
    pass


class IMAPAuthError(IMAPError):
    """Authentication failed."""
    pass


class IMAPConnectionError(IMAPError):
    """Connection failed."""
    pass


class IMAPSSLError(IMAPError):
    """SSL/TLS error."""
    pass


@dataclass
class IMAPFolder:
    """Represents an IMAP folder/mailbox."""
    name: str
    delimiter: str
    flags: list[str]
    total_messages: int = 0
    unread_messages: int = 0
    recent_messages: int = 0


@dataclass
class IMAPMessage:
    """Represents an IMAP message metadata."""
    uid: str
    message_id: str
    subject: str
    from_address: str
    from_name: str
    to_addresses: list[dict]
    cc_addresses: list[dict]
    date_sent: Optional[datetime]
    date_received: Optional[datetime]
    preview: str
    size_bytes: int
    flags: list[str]
    has_attachments: bool
    in_reply_to: str
    references: list[str]
    # MAIL-19: plain-text body for search_vector weight D (None if not fetched).
    body_text: Optional[str] = None


class IMAPClient:
    """
    IMAP client for connecting to any IMAP server.

    Supports:
    - SSL/TLS (port 993)
    - STARTTLS (port 143)
    - No encryption (port 143)
    """

    # Standard folder names to roles mapping
    FOLDER_ROLES = {
        'inbox': 'inbox',
        'sent': 'sent',
        'sent mail': 'sent',
        'sent items': 'sent',
        'drafts': 'drafts',
        'draft': 'drafts',
        'trash': 'trash',
        'deleted items': 'trash',
        'deleted messages': 'trash',
        'bin': 'trash',
        'junk': 'junk',
        'spam': 'junk',
        'junk mail': 'junk',
        'junk e-mail': 'junk',
        'archive': 'archive',
        'all mail': 'archive',
        '[gmail]/all mail': 'archive',
        '[gmail]/sent mail': 'sent',
        '[gmail]/drafts': 'drafts',
        '[gmail]/spam': 'junk',
        '[gmail]/trash': 'trash',
    }

    def __init__(
        self,
        host: str,
        port: int = 993,
        username: str = None,
        password: str = None,
        security: str = 'ssl',  # 'ssl', 'starttls', 'none'
        timeout: int = 30,
    ):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.security = security
        self.timeout = timeout
        self._connection: Optional[imaplib.IMAP4] = None
        self._connected = False

    def connect(self) -> bool:
        """
        Establish connection to the IMAP server.

        Returns:
            True if connection successful

        Raises:
            IMAPConnectionError: If connection fails
            IMAPSSLError: If SSL/TLS setup fails
            IMAPAuthError: If authentication fails
        """
        try:
            from django.conf import settings
            verify_ssl = getattr(settings, 'STALWART_VERIFY_SSL', True)

            if self.security == 'ssl':
                # Direct SSL connection (typically port 993)
                context = ssl.create_default_context()
                if not verify_ssl:
                    context.check_hostname = False
                    context.verify_mode = ssl.CERT_NONE
                self._connection = imaplib.IMAP4_SSL(
                    self.host,
                    self.port,
                    ssl_context=context,
                    timeout=self.timeout
                )
            else:
                # Plain connection (typically port 143)
                self._connection = imaplib.IMAP4(
                    self.host,
                    self.port,
                    timeout=self.timeout
                )

                if self.security == 'starttls':
                    # Upgrade to TLS
                    context = ssl.create_default_context()
                    if not verify_ssl:
                        context.check_hostname = False
                        context.verify_mode = ssl.CERT_NONE
                    self._connection.starttls(ssl_context=context)

        except ssl.SSLError as e:
            raise IMAPSSLError(f"SSL/TLS error connecting to {self.host}:{self.port}: {e}")
        except (OSError, imaplib.IMAP4.error) as e:
            raise IMAPConnectionError(f"Failed to connect to {self.host}:{self.port}: {e}")

        # Authenticate
        if self.username and self.password:
            try:
                self._connection.login(self.username, self.password)
            except imaplib.IMAP4.error as e:
                self._connection.logout()
                raise IMAPAuthError(f"Authentication failed: {e}")

        self._connected = True
        return True

    def disconnect(self):
        """Close the IMAP connection."""
        if self._connection:
            try:
                self._connection.logout()
            except Exception:
                pass
            self._connection = None
            self._connected = False

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.disconnect()
        return False

    def _ensure_connected(self):
        """Ensure we have an active connection."""
        if not self._connected or not self._connection:
            raise IMAPError("Not connected to IMAP server")

    def test_connection(self) -> tuple[bool, str]:
        """
        Test the IMAP connection with current credentials.

        Returns:
            Tuple of (success: bool, message: str)
        """
        try:
            self.connect()
            # Try to select INBOX to verify access
            self._connection.select('INBOX', readonly=True)
            self._connection.close()
            self.disconnect()
            return True, "Connection successful"
        except IMAPAuthError as e:
            return False, f"Authentication failed: {e}"
        except IMAPSSLError as e:
            return False, f"SSL/TLS error: {e}"
        except IMAPConnectionError as e:
            return False, f"Connection failed: {e}"
        except Exception as e:
            return False, f"Error: {e}"
        finally:
            self.disconnect()

    def list_folders(self) -> list[IMAPFolder]:
        """
        List all folders in the mailbox.

        Returns:
            List of IMAPFolder objects
        """
        self._ensure_connected()

        folders = []
        status, folder_list = self._connection.list()

        if status != 'OK':
            raise IMAPError(f"Failed to list folders: {status}")

        for folder_data in folder_list:
            if folder_data is None:
                continue

            # Parse folder response: (\\flags) "delimiter" "name"
            if isinstance(folder_data, bytes):
                folder_data = folder_data.decode('utf-8', errors='replace')

            match = re.match(r'\(([^)]*)\)\s+"([^"]+)"\s+"?([^"]+)"?', folder_data)
            if not match:
                continue

            flags_str, delimiter, name = match.groups()
            flags = [f.strip() for f in flags_str.split() if f.strip()]

            # Get folder status (message counts)
            total = unread = recent = 0
            try:
                status, data = self._connection.status(
                    f'"{name}"',
                    '(MESSAGES UNSEEN RECENT)'
                )
                if status == 'OK' and data[0]:
                    status_str = data[0].decode('utf-8') if isinstance(data[0], bytes) else data[0]
                    messages_match = re.search(r'MESSAGES\s+(\d+)', status_str)
                    unseen_match = re.search(r'UNSEEN\s+(\d+)', status_str)
                    recent_match = re.search(r'RECENT\s+(\d+)', status_str)
                    if messages_match:
                        total = int(messages_match.group(1))
                    if unseen_match:
                        unread = int(unseen_match.group(1))
                    if recent_match:
                        recent = int(recent_match.group(1))
            except Exception as e:
                logger.warning(f"Could not get status for folder {name}: {e}")

            folders.append(IMAPFolder(
                name=name,
                delimiter=delimiter,
                flags=flags,
                total_messages=total,
                unread_messages=unread,
                recent_messages=recent,
            ))

        return folders

    def get_folder_role(self, folder_name: str) -> str:
        """
        Determine the role of a folder based on its name.

        Returns:
            Role string ('inbox', 'sent', 'drafts', 'trash', 'junk', 'archive', 'custom')
        """
        name_lower = folder_name.lower()
        return self.FOLDER_ROLES.get(name_lower, 'custom')

    def select_folder(self, folder_name: str, readonly: bool = False) -> dict:
        """
        Select a folder for subsequent operations.

        Returns:
            Dict with folder info (exists, recent, flags)
        """
        self._ensure_connected()

        if readonly:
            # Use select with readonly flag (examine was added in Python 3.12)
            status, data = self._connection.select(folder_name, readonly=True)
        else:
            status, data = self._connection.select(folder_name)

        if status != 'OK':
            raise IMAPError(f"Failed to select folder {folder_name}: {status}")

        # MAIL-7: UIDVALIDITY (the untagged SELECT/EXAMINE response) — IMAP UIDs
        # are only stable WITHIN a UIDVALIDITY epoch; when the server changes it,
        # every prior UID is meaningless and the folder must be re-mapped rather
        # than UID-deduped against stale rows.
        uidvalidity = None
        try:
            typ, uv = self._connection.response('UIDVALIDITY')
            if uv and uv[0]:
                raw = uv[0].decode() if isinstance(uv[0], bytes) else str(uv[0])
                uidvalidity = int(raw)
        except (ValueError, TypeError, AttributeError):
            uidvalidity = None

        return {
            'exists': int(data[0]) if data[0] else 0,
            'uidvalidity': uidvalidity,
        }

    def fetch_messages(
        self,
        folder_name: str,
        limit: int = 50,
        offset: int = 0,
        search_criteria: str = 'ALL',
    ) -> list[IMAPMessage]:
        """
        Fetch messages from a folder.

        Args:
            folder_name: Name of the folder to fetch from
            limit: Maximum number of messages to fetch
            offset: Number of messages to skip
            search_criteria: IMAP search criteria (default: 'ALL')

        Returns:
            List of IMAPMessage objects
        """
        self._ensure_connected()

        self.select_folder(folder_name, readonly=True)

        # Search for messages
        status, data = self._connection.uid('SEARCH', None, search_criteria)
        if status != 'OK':
            raise IMAPError(f"Search failed: {status}")

        uids = data[0].split() if data[0] else []
        # Sort by UID descending (newest first) and apply pagination
        uids = sorted(uids, reverse=True)
        uids = uids[offset:offset + limit]

        if not uids:
            return []

        # Fetch message headers — use HEADER.FIELDS for reliable parsing
        uid_str = b','.join(uids)
        fetch_items = '(UID FLAGS RFC822.SIZE BODY.PEEK[HEADER.FIELDS (FROM TO CC SUBJECT DATE MESSAGE-ID IN-REPLY-TO REFERENCES CONTENT-TYPE)])'
        status, data = self._connection.uid('FETCH', uid_str, fetch_items)

        if status != 'OK':
            raise IMAPError(f"Fetch failed: {status}")

        messages = []
        uid_to_top_content_type: dict[str, str] = {}

        for item in data:
            if item is None or not isinstance(item, tuple):
                continue
            if len(item) < 2:
                continue

            response_part = item[0]
            if isinstance(response_part, bytes):
                response_part = response_part.decode('utf-8', errors='replace')

            # Extract UID
            uid_match = re.search(r'UID\s+(\d+)', response_part)
            if not uid_match:
                continue
            uid = uid_match.group(1)

            # Extract FLAGS
            flags = []
            flags_match = re.search(r'FLAGS\s*\(([^)]*)\)', response_part)
            if flags_match:
                flags = flags_match.group(1).split()

            # Extract size
            size = 0
            size_match = re.search(r'RFC822\.SIZE\s+(\d+)', response_part)
            if size_match:
                size = int(size_match.group(1))

            # Parse headers from the body part
            header_text = ''
            if isinstance(item[1], bytes):
                header_text = item[1].decode('utf-8', errors='replace')
            headers = self._parse_headers(header_text)

            # Parse From header
            from_raw = headers.get('from', '')
            from_name = ''
            from_address = from_raw
            if '<' in from_raw:
                name_match = re.match(r'^"?([^"<]*)"?\s*<', from_raw)
                email_match = re.search(r'<([^>]+)>', from_raw)
                if name_match:
                    from_name = name_match.group(1).strip()
                if email_match:
                    from_address = email_match.group(1).strip()

            # Parse To/CC as lists
            to_addresses = []
            for addr in (headers.get('to', '') or '').split(','):
                addr = addr.strip()
                if addr:
                    to_addresses.append({'email': addr, 'name': ''})

            cc_addresses = []
            for addr in (headers.get('cc', '') or '').split(','):
                addr = addr.strip()
                if addr:
                    cc_addresses.append({'email': addr, 'name': ''})

            # Parse date
            date_sent = None
            date_str = headers.get('date', '')
            if date_str:
                try:
                    from email.utils import parsedate_to_datetime
                    date_sent = parsedate_to_datetime(date_str)
                except Exception:
                    pass

            # Detect attachments from Content-Type
            content_type = headers.get('content-type', '').lower()
            uid_to_top_content_type[uid] = content_type
            has_attachments = 'multipart/mixed' in content_type

            messages.append(IMAPMessage(
                uid=uid,
                message_id=headers.get('message-id', '').strip('<> '),
                subject=self._decode_header(headers.get('subject', '')),
                from_address=from_address,
                from_name=self._decode_header(from_name),
                to_addresses=to_addresses,
                cc_addresses=cc_addresses,
                date_sent=date_sent,
                date_received=date_sent,
                preview='',
                size_bytes=size,
                flags=flags,
                has_attachments=has_attachments,
                in_reply_to=headers.get('in-reply-to', '').strip('<> '),
                references=headers.get('references', []) if isinstance(headers.get('references'), list) else [],
            ))

        # MAIL-19 / MAIL-29: second batch fetch for the first text part only.
        # BODY.PEEK[TEXT] returns the whole MIME body for multipart messages
        # (boundaries + HTML/attachments). BODY.PEEK[1] targets the first MIME
        # part, conventionally text/plain for multipart/alternative. Fetch the
        # part MIME header too when available so non-text first parts are not
        # indexed into the body_text FTS cache.
        try:
            uid_to_text = self._fetch_first_plain_text_parts(
                uid_str,
                messages,
                uid_to_top_content_type,
            )
            for msg in messages:
                msg.body_text = uid_to_text.get(msg.uid)
        except Exception:
            pass  # fail-open: body_text stays None; backfill handles it later

        return messages

    @staticmethod
    def _decode_imap_literal(value: bytes | str) -> str:
        return value.decode('utf-8', errors='replace') if isinstance(value, bytes) else value

    def _fetch_first_plain_text_parts(
        self,
        uid_str: bytes,
        messages: list[IMAPMessage],
        uid_to_top_content_type: dict[str, str],
    ) -> dict[str, str]:
        uid_to_text: dict[str, str] = {}
        uid_to_part_mime: dict[str, str] = {}
        message_uids = {msg.uid for msg in messages}
        last_uid: str | None = None

        status, body_data = self._connection.uid(
            'FETCH',
            uid_str,
            '(UID BODY.PEEK[1.MIME] BODY.PEEK[1])',
        )
        if status != 'OK' or not body_data:
            return uid_to_text

        # BODY[1.MIME] and BODY[1] often arrive as separate literals; the second
        # literal may omit UID in its metadata, so keep the most recent UID.
        for bitem in body_data:
            if not isinstance(bitem, tuple) or len(bitem) < 2:
                continue
            meta = self._decode_imap_literal(bitem[0]) if isinstance(bitem[0], (bytes, str)) else ''
            uid_match = re.search(r'UID\s+(\d+)', meta)
            if uid_match:
                candidate_uid = uid_match.group(1)
                if candidate_uid not in message_uids:
                    last_uid = None
                    continue
                last_uid = candidate_uid
            uid = last_uid
            if not uid or not isinstance(bitem[1], (bytes, str)):
                continue

            literal = self._decode_imap_literal(bitem[1])
            if literal.strip().upper() == 'NIL':
                continue
            section = meta.upper()
            if 'BODY[1.MIME]' in section:
                uid_to_part_mime[uid] = literal
                continue
            if 'BODY[1]' in section:
                if self._is_text_plain_part(
                    uid_to_part_mime.get(uid),
                    uid_to_top_content_type.get(uid, ''),
                ):
                    uid_to_text[uid] = literal

        return uid_to_text

    @staticmethod
    def _is_text_plain_part(part_mime_headers: str | None, top_content_type: str = '') -> bool:
        if not part_mime_headers:
            # Single-part text/plain servers may not return BODY[1.MIME]; keep
            # plain-text behavior, but do not index known non-text or multipart
            # bodies when the first part's MIME header is unavailable.
            top = (top_content_type or '').split(';', 1)[0].strip().lower()
            return top in ('', 'text/plain')
        part = email.message_from_string(part_mime_headers)
        return part.get_content_type().lower() == 'text/plain'

    def fetch_message(self, folder_name: str, uid: str, include_body: bool = True) -> dict:
        """
        Fetch a single message with full details.

        Args:
            folder_name: Name of the folder
            uid: Message UID
            include_body: Whether to fetch the full body

        Returns:
            Dict with message details including body
        """
        self._ensure_connected()

        self.select_folder(folder_name, readonly=True)

        if include_body:
            fetch_items = '(UID FLAGS RFC822)'
        else:
            fetch_items = '(UID FLAGS RFC822.HEADER)'

        status, data = self._connection.uid('FETCH', uid, fetch_items)

        if status != 'OK':
            raise IMAPError(f"Fetch failed: {status}")

        # Parse the email
        for item in data:
            if isinstance(item, tuple) and len(item) > 1:
                raw_email = item[1]
                if isinstance(raw_email, bytes):
                    msg = email.message_from_bytes(raw_email)
                    return self._parse_email_message(msg, uid)

        raise IMAPError(f"Message {uid} not found")

    def move_messages(self, folder_name: str, uids: list[str], target_folder: str):
        """
        Move messages to another folder.

        Args:
            folder_name: Source folder
            uids: List of message UIDs to move
            target_folder: Destination folder
        """
        self._ensure_connected()

        self.select_folder(folder_name, readonly=False)

        uid_str = ','.join(uids)

        # Copy to destination
        status, data = self._connection.uid('COPY', uid_str, target_folder)
        if status != 'OK':
            raise IMAPError(f"Copy failed: {status}")

        # Mark as deleted in source
        status, data = self._connection.uid('STORE', uid_str, '+FLAGS', '(\\Deleted)')
        if status != 'OK':
            raise IMAPError(f"Mark deleted failed: {status}")

        # Expunge to remove deleted messages
        self._connection.expunge()

    def delete_messages(self, folder_name: str, uids: list[str]):
        """
        Permanently delete messages.

        Args:
            folder_name: Folder containing messages
            uids: List of message UIDs to delete
        """
        self._ensure_connected()

        self.select_folder(folder_name, readonly=False)

        uid_str = ','.join(uids)

        # Mark as deleted
        status, data = self._connection.uid('STORE', uid_str, '+FLAGS', '(\\Deleted)')
        if status != 'OK':
            raise IMAPError(f"Mark deleted failed: {status}")

        # Expunge
        self._connection.expunge()

    def set_flags(self, folder_name: str, uids: list[str], add_flags: list[str] = None, remove_flags: list[str] = None):
        """
        Set or remove flags on messages.

        Common flags: \\Seen, \\Flagged, \\Answered, \\Draft, \\Deleted
        """
        self._ensure_connected()

        self.select_folder(folder_name, readonly=False)

        uid_str = ','.join(uids)

        if add_flags:
            flags_str = ' '.join(add_flags)
            status, data = self._connection.uid('STORE', uid_str, '+FLAGS', f'({flags_str})')
            if status != 'OK':
                raise IMAPError(f"Add flags failed: {status}")

        if remove_flags:
            flags_str = ' '.join(remove_flags)
            status, data = self._connection.uid('STORE', uid_str, '-FLAGS', f'({flags_str})')
            if status != 'OK':
                raise IMAPError(f"Remove flags failed: {status}")

    def mark_read(self, folder_name: str, uids: list[str]):
        """Mark messages as read."""
        self.set_flags(folder_name, uids, add_flags=['\\Seen'])

    def mark_unread(self, folder_name: str, uids: list[str]):
        """Mark messages as unread."""
        self.set_flags(folder_name, uids, remove_flags=['\\Seen'])

    def mark_flagged(self, folder_name: str, uids: list[str]):
        """Mark messages as flagged/starred."""
        self.set_flags(folder_name, uids, add_flags=['\\Flagged'])

    def mark_unflagged(self, folder_name: str, uids: list[str]):
        """Remove flagged/starred status."""
        self.set_flags(folder_name, uids, remove_flags=['\\Flagged'])

    def search(self, folder_name: str, query: str, limit: int = 50) -> list[IMAPMessage]:
        """
        Search for messages.

        Args:
            folder_name: Folder to search in
            query: Search text
            limit: Maximum results

        Returns:
            List of matching messages
        """
        # Build IMAP search criteria
        search_criteria = f'OR OR SUBJECT "{query}" FROM "{query}" BODY "{query}"'
        return self.fetch_messages(folder_name, limit=limit, search_criteria=search_criteria)

    def _parse_envelope(self, response: str) -> dict:
        """Parse IMAP ENVELOPE structure."""
        result = {
            'date': None,
            'subject': '',
            'from_address': '',
            'from_name': '',
            'to': [],
            'cc': [],
        }

        # Extract ENVELOPE
        envelope_match = re.search(r'ENVELOPE\s*\((.+?)\)\s*(?:BODY|$)', response, re.DOTALL)
        if not envelope_match:
            return result

        envelope_str = envelope_match.group(1)

        # This is a simplified parser - real ENVELOPE parsing is complex
        # For production, consider using a proper IMAP library

        # Try to extract subject
        subject_match = re.search(r'"([^"]*)"', envelope_str)
        if subject_match:
            result['subject'] = self._decode_header(subject_match.group(1))

        return result

    def _parse_headers(self, header_text: str) -> dict:
        """Parse email headers."""
        headers = {}
        current_header = None
        current_value = []

        for line in header_text.split('\n'):
            if line.startswith((' ', '\t')) and current_header:
                # Continuation of previous header
                current_value.append(line.strip())
            elif ':' in line:
                # Save previous header
                if current_header:
                    headers[current_header.lower()] = ' '.join(current_value)

                # Start new header
                name, value = line.split(':', 1)
                current_header = name.strip()
                current_value = [value.strip()]

        # Save last header
        if current_header:
            headers[current_header.lower()] = ' '.join(current_value)

        # Parse references into list
        if 'references' in headers:
            refs = re.findall(r'<[^>]+>', headers['references'])
            headers['references'] = refs

        return headers

    def _decode_header(self, header_value: str) -> str:
        """Decode a potentially encoded header value."""
        if not header_value:
            return ''

        try:
            decoded_parts = decode_header(header_value)
            result = []
            for part, encoding in decoded_parts:
                if isinstance(part, bytes):
                    result.append(part.decode(encoding or 'utf-8', errors='replace'))
                else:
                    result.append(part)
            return ''.join(result)
        except Exception:
            return header_value

    def _parse_email_message(self, msg: email.message.Message, uid: str) -> dict:
        """Parse a full email message."""
        result = {
            'uid': uid,
            'message_id': msg.get('Message-ID', ''),
            'subject': self._decode_header(msg.get('Subject', '')),
            'from': self._parse_address(msg.get('From', '')),
            'to': self._parse_address_list(msg.get('To', '')),
            'cc': self._parse_address_list(msg.get('Cc', '')),
            'bcc': self._parse_address_list(msg.get('Bcc', '')),
            'reply_to': self._parse_address(msg.get('Reply-To', '')),
            'date': msg.get('Date', ''),
            'in_reply_to': msg.get('In-Reply-To', ''),
            'references': msg.get('References', '').split(),
            'body_text': '',
            'body_html': '',
            'attachments': [],
        }

        # Parse body
        if msg.is_multipart():
            for part in msg.walk():
                content_type = part.get_content_type()
                content_disposition = str(part.get('Content-Disposition', ''))

                if 'attachment' in content_disposition:
                    # Attachment
                    filename = part.get_filename() or 'attachment'
                    result['attachments'].append({
                        'filename': self._decode_header(filename),
                        'content_type': content_type,
                        'size': len(part.get_payload(decode=True) or b''),
                    })
                elif content_type == 'text/plain':
                    payload = part.get_payload(decode=True)
                    if payload:
                        charset = part.get_content_charset() or 'utf-8'
                        result['body_text'] = payload.decode(charset, errors='replace')
                elif content_type == 'text/html':
                    payload = part.get_payload(decode=True)
                    if payload:
                        charset = part.get_content_charset() or 'utf-8'
                        result['body_html'] = payload.decode(charset, errors='replace')
        else:
            # Single part message
            content_type = msg.get_content_type()
            payload = msg.get_payload(decode=True)
            if payload:
                charset = msg.get_content_charset() or 'utf-8'
                if content_type == 'text/html':
                    result['body_html'] = payload.decode(charset, errors='replace')
                else:
                    result['body_text'] = payload.decode(charset, errors='replace')

        return result

    def _parse_address(self, address_str: str) -> dict:
        """Parse a single email address."""
        if not address_str:
            return {'email': '', 'name': ''}

        # Simple parsing - for production use email.utils.parseaddr
        match = re.match(r'(.+?)\s*<([^>]+)>', address_str)
        if match:
            name = self._decode_header(match.group(1).strip(' "'))
            email_addr = match.group(2).strip()
            return {'email': email_addr, 'name': name}

        return {'email': address_str.strip(), 'name': ''}

    def _parse_address_list(self, addresses_str: str) -> list[dict]:
        """Parse a comma-separated list of email addresses."""
        if not addresses_str:
            return []

        addresses = []
        # Split on comma but not within quotes or angle brackets
        parts = re.split(r',\s*(?=(?:[^"]*"[^"]*")*[^"]*$)', addresses_str)
        for part in parts:
            if part.strip():
                addresses.append(self._parse_address(part.strip()))

        return addresses
