"""MAIL-24: shared IMAP/SMTP error classification.

Single source of truth that maps a connection failure to a `connection_status`
enum value (see mail/models.py CONNECTION_STATUS_CHOICES): `auth_failed`,
`needs_reauth`, `ssl_error`, or `connection_failed`. Used by BOTH the Celery
sync tasks (pass the exception) and the `test_connection` view (pass `message=`)
so the two paths never drift into separate classifiers (HLD MAIL-2 §4.1, R-3).

When given a `message` only (the test_connection path) the classifier applies the
exact substring rules test_connection has always used — `auth` → `ssl` → else — so
that path's behaviour is unchanged. When given an exception (the sync path) it
checks the exception *type* first for reliability, then falls back to those same
substring rules.

MAIL-28: pass `auth_type='oauth2'` for OAuth accounts so that authentication
failures return `'needs_reauth'` (token-expiry / re-authorization needed) instead
of the generic `'auth_failed'`. OAuth users have no password to correct; the
distinction lets the UI surface the right re-authorization prompt.
"""
import imaplib
import socket
import ssl

# Persistent failures: surface for re-auth/attention, NOT eligible for auto-retry.
# (MAIL-27 wires `autoretry_for` for the transient `connection_failed` case only.)
# `needs_reauth` is persistent: the user must re-authorize before syncing can resume.
PERSISTENT_STATUSES = frozenset({'auth_failed', 'ssl_error', 'needs_reauth'})


class TransientSyncError(Exception):
    """Transient IMAP/JMAP sync failure — eligible for Celery autoretry (MAIL-27)."""


def classify_connection_error(error=None, *, message=None, auth_type='password') -> str:
    """Return a `connection_status` value for an IMAP/SMTP failure.

    error:     the caught exception (sync path) — type is checked first.
    message:   a diagnostic string (test_connection path) — substring rules only.
    auth_type: 'oauth2' or 'password' (default). OAuth auth failures return
               'needs_reauth' instead of 'auth_failed' (MAIL-28).
    """
    text = (message if message is not None else (str(error) if error is not None else '')).lower()

    # Exception-type detection (reliable; only runs when an exception is supplied).
    if error is not None:
        if isinstance(error, ssl.SSLError):
            return 'ssl_error'
        if isinstance(error, (socket.timeout, TimeoutError, ConnectionError, socket.gaierror)):
            return 'connection_failed'
        if isinstance(error, imaplib.IMAP4.error) and ('auth' in text or 'credential' in text):
            return 'needs_reauth' if auth_type == 'oauth2' else 'auth_failed'

    # Substring rules — identical to the legacy test_connection logic (auth before ssl).
    if 'auth' in text:
        return 'needs_reauth' if auth_type == 'oauth2' else 'auth_failed'
    if 'ssl' in text:
        return 'ssl_error'
    return 'connection_failed'
