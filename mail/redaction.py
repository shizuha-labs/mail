"""Secret-safe redaction helpers for Mail logging, errors, and traces.

MAIL-13 follows the platform redaction pattern from PLAT-194: sanitize as close
as possible to log/error boundaries, preserve coarse diagnostic shape, and never
emit credential-adjacent values. The only shared utility currently present in
this workspace is Pulse's webhook-path redactor (``tasks.log_redaction``), which
is not an importable standalone package for Mail; MAIL-13 links that platform
packaging gap in Pulse before using this Mail-local fallback.
"""
from __future__ import annotations

import hashlib
import logging
import re
import uuid
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

REDACTED = '<redacted>'
EMAIL_REDACTED = '<email:redacted>'

_SENSITIVE_KEY_RE = re.compile(
    r'(password|passwd|pwd|secret|credential|credentials|token|access[_-]?token|refresh[_-]?token|'
    r'id[_-]?token|auth|authorization|cookie|session|csrf|oauth|code|'
    r'client[_-]?secret|api[_-]?key|smtp|imap|jmap|connection[_-]?string|body[_-]?text)',
    re.IGNORECASE,
)

_EMAIL_RE = re.compile(r'(?<![\w.+-])([A-Za-z0-9._%+-]+)@([A-Za-z0-9.-]+\.[A-Za-z]{2,})(?![\w.-])')
_BEARER_RE = re.compile(r'\b(Bearer|Basic)\s+[A-Za-z0-9+/=_:.-]{8,}', re.IGNORECASE)
_ASSIGNMENT_RE = re.compile(
    r'(?P<key>\b(?:access[_-]?token|refresh[_-]?token|id[_-]?token|token|password|passwd|pwd|secret|credentials?|'
    r'client[_-]?secret|api[_-]?key|code|authorization|cookie|session)\b)'
    r'(?P<sep>\s*[:=]\s*)'
    r'(?P<quote>["\']?)'
    r'(?P<value>[^\s,"\'&;)}\]%]+)'
    r'(?P=quote)',
    re.IGNORECASE,
)
_URL_CREDENTIAL_RE = re.compile(r'(?P<prefix>\b[a-z][a-z0-9+.-]*://[^\s/@:]+:)[^\s/@]+(?=@)', re.IGNORECASE)
_LONG_TOKEN_RE = re.compile(r'(?<![A-Za-z0-9_=-])([A-Za-z0-9_-]{32,})(?![A-Za-z0-9_=-])')
_UUID_RE = re.compile(
    r'(?<![A-Za-z0-9])([0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})(?![A-Za-z0-9])',
    re.IGNORECASE,
)

_LOG_RECORD_BUILTINS = frozenset(logging.LogRecord(
    name='', level=0, pathname='', lineno=0, msg='', args=(), exc_info=None,
).__dict__) | {'message', 'asctime'}


def _stable_email_marker(match: re.Match[str]) -> str:
    digest = hashlib.sha256(match.group(0).lower().encode('utf-8')).hexdigest()[:8]
    return f'<email:{digest}>'


def _stable_id_marker(value: str) -> str:
    digest = hashlib.sha256(value.lower().encode('utf-8')).hexdigest()[:8]
    return f'<id:{digest}>'


def _stable_uuid_marker(match: re.Match[str]) -> str:
    return _stable_id_marker(match.group(1))


def redact_email(value: str) -> str:
    """Redact account-sensitive email identifiers while preserving correlation."""
    return _EMAIL_RE.sub(_stable_email_marker, value)


def _redact_query_string(value: str) -> str:
    if '?' not in value and '&' not in value:
        return value
    try:
        split = urlsplit(value)
    except ValueError:
        return value
    if not split.query:
        return value
    query = []
    changed = False
    for key, item in parse_qsl(split.query, keep_blank_values=True):
        if _SENSITIVE_KEY_RE.search(key):
            query.append((key, REDACTED))
            changed = True
        else:
            query.append((key, item))
    if not changed:
        return value
    return urlunsplit((split.scheme, split.netloc, split.path, urlencode(query), split.fragment))


def redact_sensitive(value: Any, *, redact_emails: bool = True) -> Any:
    """Return *value* with credentials, tokens, OAuth codes, and IDs redacted.

    Handles strings plus the tuple/list/dict containers commonly attached to
    logging records, DRF validation errors, provider JSON bodies, and traces.
    """
    if isinstance(value, str):
        redacted = _redact_query_string(value)
        redacted = _URL_CREDENTIAL_RE.sub(lambda match: f"{match.group('prefix')}{REDACTED}", redacted)
        redacted = _BEARER_RE.sub(lambda match: f"{match.group(1)} {REDACTED}", redacted)
        redacted = _ASSIGNMENT_RE.sub(lambda match: f"{match.group('key')}{match.group('sep')}{match.group('quote')}{REDACTED}{match.group('quote')}", redacted)
        redacted = _LONG_TOKEN_RE.sub(REDACTED, redacted)
        redacted = _UUID_RE.sub(_stable_uuid_marker, redacted)
        if redact_emails:
            redacted = redact_email(redacted)
        return redacted
    if isinstance(value, uuid.UUID):
        return _stable_id_marker(str(value))
    if isinstance(value, bytes):
        return redact_sensitive(value.decode('utf-8', errors='replace'), redact_emails=redact_emails)
    if isinstance(value, tuple):
        return tuple(redact_sensitive(item, redact_emails=redact_emails) for item in value)
    if isinstance(value, list):
        return [redact_sensitive(item, redact_emails=redact_emails) for item in value]
    if isinstance(value, Mapping):
        safe: dict[Any, Any] = {}
        for key, item in value.items():
            if _SENSITIVE_KEY_RE.search(str(key)):
                safe[key] = REDACTED
            else:
                safe[key] = redact_sensitive(item, redact_emails=redact_emails)
        return safe
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray, str)):
        return type(value)(redact_sensitive(item, redact_emails=redact_emails) for item in value)
    return value


def safe_exception_text(exc: BaseException | str) -> str:
    """Sanitize an exception for logs only; public responses should be generic."""
    return str(redact_sensitive(str(exc)))


def public_provider_error(default: str = 'Mail provider request failed') -> str:
    """Generic user-visible provider failure shape."""
    return default


class MailRedactionFilter(logging.Filter):
    """Logging filter that redacts messages, args, and common extra fields."""

    def filter(self, record: logging.LogRecord) -> bool:
        # Redact after stdlib %-formatting so key context in the message
        # (for example ``credential=%s``) can protect short values that would
        # not match standalone token-length heuristics.
        record.msg = redact_sensitive(record.getMessage())
        record.args = ()
        for attr, value in list(record.__dict__.items()):
            if attr not in _LOG_RECORD_BUILTINS:
                setattr(
                    record,
                    attr,
                    REDACTED if _SENSITIVE_KEY_RE.search(attr) else redact_sensitive(value),
                )
        return True
