"""
Celery tasks for Shizuha Mail.

Protocol-aware dispatch:
- IMAP accounts (Gmail, Outlook, Yahoo, etc.) → IMAPClient
- JMAP accounts (Stalwart self-hosted) → StalwartJMAPClient
"""
import asyncio
import html
import html.parser
import io
import logging
import os
import re
import time
import requests
from datetime import timedelta

from .redaction import redact_sensitive, safe_exception_text
from .sync_utils import classify_connection_error, PERSISTENT_STATUSES, TransientSyncError
from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)

# MAIL-19: cap body_text to bound trigger cost and index size.
_BODY_TEXT_MAX_BYTES = 256 * 1024  # 256 KB


class _HTMLTextExtractor(html.parser.HTMLParser):
    """Minimal HTML → plain-text extractor (standard-library only)."""
    _SKIP_TAGS = frozenset({'script', 'style', 'head', 'meta', 'link'})

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag.lower() in self._SKIP_TAGS:
            self._skip_depth += 1
        elif tag.lower() in ('br', 'p', 'div', 'tr', 'li', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6'):
            self._parts.append('\n')

    def handle_endtag(self, tag):
        if tag.lower() in self._SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data):
        if self._skip_depth == 0:
            self._parts.append(data)

    def get_text(self) -> str:
        return re.sub(r'\n{3,}', '\n\n', ''.join(self._parts)).strip()


def _strip_html(text: str) -> str:
    """Return plain text from an HTML or plain-text string."""
    if not text:
        return ''
    stripped = text.strip()
    if not stripped.startswith('<'):
        # Plain-text MIME parts can still carry HTML character references.
        # Decode them without treating the decoded value as markup (for
        # example ``&lt;hello&gt;`` must remain the literal text ``<hello>``).
        return html.unescape(stripped)
    extractor = _HTMLTextExtractor()
    try:
        extractor.feed(stripped)
        return extractor.get_text()
    except Exception:
        # Fall back to naive tag removal
        return re.sub(r'<[^>]+>', ' ', stripped).strip()


def _cap_body_text(text: str) -> tuple[str, bool]:
    """Truncate *text* to _BODY_TEXT_MAX_BYTES UTF-8 bytes.

    Returns ``(capped_text, was_truncated)``.
    """
    encoded = text.encode('utf-8', errors='replace')
    if len(encoded) <= _BODY_TEXT_MAX_BYTES:
        return text, False
    return encoded[:_BODY_TEXT_MAX_BYTES].decode('utf-8', errors='replace'), True

# Daemon webhook URL for new mail alerts
# Docker gateway IP for reaching the host from inside containers
# Falls back to host.docker.internal, then localhost
DAEMON_WEBHOOK_URL = os.environ.get('SHIZUHA_DAEMON_WEBHOOK_URL', 'http://172.18.0.1:8016/v1/webhooks/mail')


# ── Protocol dispatch helpers ──

def _is_jmap_account(account) -> bool:
    return account.incoming_server_type == 'jmap'


def _is_managed_identity(account) -> bool:
    """Managed accounts require a caller-scoped delegated token."""
    return bool((getattr(account, 'settings', None) or {}).get('managed_identity'))


def _get_imap_client(account):
    from .services.imap_client import IMAPClient
    from .services.credential_service import get_credential_service
    cred = get_credential_service()
    password = cred.decrypt_password(account.encrypted_incoming_password)
    return IMAPClient(
        host=account.incoming_host,
        port=account.incoming_port,
        username=account.incoming_username or account.email_address,
        password=password,
        security=account.incoming_security,
    )


def _get_stalwart_client(account):
    try:
        from .services.stalwart_client import StalwartJMAPClient
        from .services.credential_service import DecryptionError, get_credential_service
        cred = get_credential_service()
        password = cred.decrypt_password(account.encrypted_incoming_password)
        return StalwartJMAPClient(user_email=account.email_address, user_password=password)
    except DecryptionError as e:
        logger.error("Stalwart credential decrypt failed for account %s; denying sync: %s", redact_sensitive(account.id), safe_exception_text(e))
        raise
    except ImportError as e:
        logger.error("StalwartJMAPClient unavailable: %s", safe_exception_text(e))
        return None
    except Exception as e:
        logger.error("StalwartJMAPClient unavailable: %s", safe_exception_text(e))
        return None


# ── Sync tasks ──

def _get_active_job_account(account_id: str, expected_user_id=None):
    """Resolve a queued job and reject accounts that need request-scoped auth."""
    from .models import MailAccount

    qs = MailAccount.objects.filter(id=account_id, is_active=True)
    if expected_user_id is not None:
        qs = qs.filter(user_id=expected_user_id)
    account = qs.first()
    if not account:
        logger.warning(
            'Skipping mail job for inactive, missing, or owner-mismatched account_id=%s',
            account_id,
        )
        return None
    if _is_managed_identity(account):
        # A Celery payload has no authenticated caller and must never fall back
        # to the legacy encrypted-password/admin-token path for managed mail.
        logger.info(
            'Skipping background mail sync for managed account_id=%s; '
            'an authenticated request-scoped sync is required',
            account_id,
        )
        return None
    return account


def _account_is_still_active(account) -> bool:
    from .models import MailAccount
    return MailAccount.objects.filter(id=account.id, user_id=account.user_id, is_active=True).exists()


@shared_task(
    bind=True,
    autoretry_for=(TransientSyncError,),
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
    max_retries=5,
)
def sync_mailbox_counts(self, account_id: str, expected_user_id=None):
    """Sync folder message counts. Dispatches to IMAP or JMAP."""
    from .models import MailAccount

    account = _get_active_job_account(account_id, expected_user_id)
    if not account:
        return 0

    try:
        if _is_jmap_account(account):
            _sync_jmap_folder_counts(account)
        else:
            _sync_imap_folder_counts(account)

        if _account_is_still_active(account):
            # MAIL-24 (R-4): record a truthful last successful sync timestamp + clear error.
            account.connection_status = 'connected'
            account.connection_error = ''
            account.last_sync_at = timezone.now()
            account.save(update_fields=['connection_status', 'connection_error', 'last_sync_at', 'updated_at'])
        logger.info('Synced folder counts for %s', redact_sensitive(account.email_address))
    except Exception as e:
        # MAIL-24 (R-3): classify the failure (auth/ssl/transient) instead of a blanket
        # connection_failed, and persist a redacted error message (MAIL-8 redaction).
        status = classify_connection_error(e, auth_type=account.auth_type)
        logger.error('Failed to sync folder counts for %s (%s): %s', redact_sensitive(account_id), status, safe_exception_text(e))
        try:
            MailAccount.objects.filter(id=account_id, is_active=True).update(
                connection_status=status,
                connection_error=redact_sensitive(safe_exception_text(e)),
            )
        except Exception:
            pass
        # MAIL-27: transient failures trigger autoretry via TransientSyncError.
        # Persistent (auth_failed, needs_reauth, ssl_error) are surfaced via status and NOT retried.
        # On final retry exhaustion, escalate to needs_attention.
        if status not in PERSISTENT_STATUSES:
            if self.request.retries >= self.max_retries:
                MailAccount.objects.filter(id=account_id, is_active=True).update(
                    connection_status='needs_attention',
                    connection_error=redact_sensitive(safe_exception_text(e)),
                )
                return
            raise TransientSyncError(str(e)) from e


@shared_task(
    bind=True,
    autoretry_for=(TransientSyncError,),
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
    max_retries=5,
)
def sync_new_messages(self, account_id: str, folder_id: str = None, since: str = None, expected_user_id=None):
    """Fetch new messages. Dispatches to IMAP or JMAP."""
    from .models import MailAccount

    account = _get_active_job_account(account_id, expected_user_id)
    if not account:
        return 0

    try:
        if _is_jmap_account(account):
            new_count = _sync_jmap_messages(account, folder_id, since)
        else:
            new_count = _sync_imap_messages(account, folder_id, since)

        if not _account_is_still_active(account):
            logger.warning('Account %s was revoked during sync; suppressing completion side effects', redact_sensitive(account_id))
            return 0

        # MAIL-24 (R-4): record a truthful last successful sync timestamp + clear error.
        account.connection_status = 'connected'
        account.connection_error = ''
        account.last_sync_at = timezone.now()
        account.save(update_fields=['connection_status', 'connection_error', 'last_sync_at', 'updated_at'])
        logger.info('Synced messages for %s (%s new)', redact_sensitive(account.email_address), new_count)

        # Notify daemon webhook about new messages
        if new_count > 0:
            _notify_daemon_new_messages(account, new_count)

        return new_count
    except Exception as e:
        # MAIL-24 (R-3): classify (auth/ssl/transient) + persist a redacted error.
        status = classify_connection_error(e, auth_type=account.auth_type)
        logger.error('Failed to sync messages for %s (%s): %s', redact_sensitive(account_id), status, safe_exception_text(e))
        try:
            MailAccount.objects.filter(id=account_id, is_active=True).update(
                connection_status=status,
                connection_error=redact_sensitive(safe_exception_text(e)),
            )
        except Exception:
            pass
        # MAIL-27: transient failures trigger autoretry via TransientSyncError.
        # Persistent (auth_failed, needs_reauth, ssl_error) are surfaced via status and NOT retried.
        # On final retry exhaustion, escalate to needs_attention.
        if status not in PERSISTENT_STATUSES:
            if self.request.retries >= self.max_retries:
                MailAccount.objects.filter(id=account_id, is_active=True).update(
                    connection_status='needs_attention',
                    connection_error=redact_sensitive(safe_exception_text(e)),
                )
                return 0
            raise TransientSyncError(str(e)) from e
        return 0


# ── IMAP implementation ──

def _sync_imap_folder_counts(account):
    from .models import MailFolder
    client = _get_imap_client(account)
    with client:
        folders = client.list_folders()
        for folder_info in folders:
            if not _account_is_still_active(account):
                logger.warning('Account %s revoked during folder-count sync; stopping', redact_sensitive(account.id))
                break
            MailFolder.objects.update_or_create(
                account=account,
                name=folder_info.name,
                defaults={
                    'jmap_mailbox_id': f'imap:{folder_info.name}',  # unique per folder for IMAP
                    'role': client.get_folder_role(folder_info.name),
                    'total_messages': folder_info.total_messages,
                    'unread_messages': folder_info.unread_messages,
                    'updated_at': timezone.now(),
                }
            )


def _sync_imap_messages(account, folder_id=None, since=None) -> int:
    from .models import MailFolder, MailMessage, MailThread

    if not MailFolder.objects.filter(account=account).exists():
        _sync_imap_folder_counts(account)

    folders = MailFolder.objects.filter(account=account)
    if folder_id:
        folders = folders.filter(id=folder_id)
    else:
        inbox = folders.filter(role='inbox').first()
        if inbox:
            folders = MailFolder.objects.filter(id=inbox.id)

    client = _get_imap_client(account)
    total_new = 0

    with client:
        for folder in folders:
            if not _account_is_still_active(account):
                logger.warning('Account %s revoked during message sync; stopping before next folder', redact_sensitive(account.id))
                break
            try:
                search = 'ALL'
                if since:
                    from datetime import datetime
                    dt = datetime.fromisoformat(since.replace('Z', '+00:00'))
                    search = f'SINCE {dt.strftime("%d-%b-%Y")}'

                # MAIL-7: read the folder's current UIDVALIDITY and handle a
                # change cleanly. UIDs are only stable within an epoch — if the
                # server reset UIDVALIDITY, every stored imap_uid is stale and
                # the same UID number now refers to a DIFFERENT message, so we
                # must NOT UID-dedup against the old rows. Re-map: drop this
                # folder's stale-epoch IMAP rows (Message-ID dedup below still
                # prevents true duplicates of messages that re-appear), then
                # re-fetch fresh. No change → cheap incremental as before.
                sel = client.select_folder(folder.name, readonly=True)
                server_uidv = sel.get('uidvalidity') if isinstance(sel, dict) else None
                if server_uidv is not None and folder.uidvalidity not in (None, server_uidv):
                    logger.warning(
                        'IMAP UIDVALIDITY change for %s/%s: %s -> %s; clean re-map of folder UIDs',
                        redact_sensitive(account.email_address), folder.name, folder.uidvalidity, server_uidv
                    )
                    MailMessage.objects.filter(account=account, folder=folder).exclude(
                        uidvalidity=server_uidv
                    ).exclude(imap_uid='').delete()
                if server_uidv is not None and folder.uidvalidity != server_uidv:
                    MailFolder.objects.filter(id=folder.id).update(uidvalidity=server_uidv)
                    folder.uidvalidity = server_uidv

                # Epoch-correct UID dedup: only UIDs from the CURRENT UIDVALIDITY
                # count as "already have it" (post-reset, old UIDs never match).
                existing_uids = set(
                    MailMessage.objects.filter(
                        account=account, folder=folder, uidvalidity=server_uidv,
                    )
                    .exclude(imap_uid__isnull=True).exclude(imap_uid='')
                    .values_list('imap_uid', flat=True)
                )

                messages = client.fetch_messages(
                    folder_name=folder.name, limit=100, search_criteria=search,
                )

                if not _account_is_still_active(account):
                    logger.warning(
                        'Account %s revoked during message sync after fetch; skipping fetched batch before write',
                        redact_sensitive(account.id),
                    )
                    break

                for msg in messages:
                    uid_str = str(msg.uid) if hasattr(msg, 'uid') else ''
                    # (folder, uidvalidity, uid) idempotency — re-sync over the
                    # same range is a no-op the second time.
                    if uid_str and uid_str in existing_uids:
                        continue

                    msg_id = getattr(msg, 'message_id', '') or ''
                    # Message-ID secondary guard: a server-side MOVE / cross-folder
                    # copy re-presents the same message under a new UID — don't
                    # duplicate it locally.
                    if msg_id and MailMessage.objects.filter(account=account, message_id=msg_id).exists():
                        continue

                    thread = _get_or_create_thread(account, msg)

                    # MAIL-19: extract + cap body_text for FTS weight D.
                    raw_body = getattr(msg, 'body_text', None) or ''
                    body_plain = _strip_html(raw_body)
                    body_text_val, body_truncated = _cap_body_text(body_plain)

                    MailMessage.objects.create(
                        account=account, folder=folder, thread=thread,
                        imap_uid=uid_str,
                        uidvalidity=server_uidv,
                        message_id=msg_id,
                        # MAIL-6: persist the threading headers (previously dropped)
                        # so later replies can chain to this message by Message-ID.
                        in_reply_to=_normalize_msgid(getattr(msg, 'in_reply_to', '')),
                        references=[
                            _normalize_msgid(r) for r in (getattr(msg, 'references', []) or [])
                            if _normalize_msgid(r)
                        ],
                        subject=getattr(msg, 'subject', '') or '',
                        from_address=getattr(msg, 'from_address', '') or '',
                        from_name=getattr(msg, 'from_name', '') or '',
                        to_addresses=getattr(msg, 'to_addresses', []) or [],
                        cc_addresses=getattr(msg, 'cc_addresses', []) or [],
                        bcc_addresses=[],
                        preview=getattr(msg, 'preview', '')[:500] if getattr(msg, 'preview', None) else '',
                        body_text=body_text_val or None,
                        body_text_truncated=body_truncated,
                        size_bytes=getattr(msg, 'size_bytes', 0) or 0,
                        is_unread=not getattr(msg, 'is_read', False),
                        is_flagged=getattr(msg, 'is_flagged', False),
                        has_attachments=getattr(msg, 'has_attachments', False),
                        date_sent=getattr(msg, 'date', None),
                        date_received=getattr(msg, 'date', None) or timezone.now(),
                    )
                    total_new += 1

                # Update folder counts from DB
                total_in_folder = MailMessage.objects.filter(account=account, folder=folder).count()
                unread_in_folder = MailMessage.objects.filter(account=account, folder=folder, is_unread=True).count()
                MailFolder.objects.filter(id=folder.id).update(
                    total_messages=total_in_folder,
                    unread_messages=unread_in_folder,
                    updated_at=timezone.now(),
                )
                logger.info('IMAP sync: %s new in %s for %s', total_new, folder.name, redact_sensitive(account.email_address))

            except Exception as e:
                logger.error('IMAP sync failed for folder %s: %s', folder.name, safe_exception_text(e))
                continue

    # MAIL-163: refresh contacts from message history after ingestion.
    try:
        from .services.contact_service import sync_contacts_from_messages
        sync_contacts_from_messages(account)
    except Exception as ce:
        logger.warning(
            'MAIL-163: contact sync failed for %s: %s',
            redact_sensitive(account.email_address), safe_exception_text(ce),
        )

    return total_new


def _normalize_msgid(mid):
    """Bare Message-ID for matching — strip angle brackets + whitespace.
    Mirrors the IMAP client's storage form (message_id is stored without <>)."""
    return (mid or '').strip().strip('<>').strip()


def _clean_subject(subject):
    """Strip ALL leading Re:/Fwd:/Fw: prefixes (handles 'Re: Re: Fwd:')."""
    return re.sub(r'^((re|fwd|fw)\s*:\s*)+', '', subject or '', flags=re.IGNORECASE).strip()


def _touch_thread(thread, msg_date):
    if msg_date and (not thread.last_message_at or msg_date > thread.last_message_at):
        thread.last_message_at = msg_date
        thread.save(update_fields=['last_message_at', 'updated_at'])


def _referenced_ids(msg):
    """Ordered ancestor Message-IDs from In-Reply-To + References (deduped).
    References carries the FULL ancestor chain, so a missing immediate parent
    (split thread) still resolves via an earlier ancestor — no orphaned reply."""
    ids = []
    irt = _normalize_msgid(getattr(msg, 'in_reply_to', ''))
    if irt:
        ids.append(irt)
    for r in (getattr(msg, 'references', []) or []):
        rid = _normalize_msgid(r)
        if rid and rid not in ids:
            ids.append(rid)
    return ids


def _get_or_create_thread(account, msg):
    """MAIL-6: reconstruct conversation threads from RFC 5322 headers
    (In-Reply-To / References) FIRST, falling back to normalized-subject only
    when no header linkage is known. Header-first means a subject-change reply
    still attaches to its parent, and a missing-middle message doesn't orphan
    later replies (References walks past the gap)."""
    from .models import MailThread, MailMessage
    import uuid

    msg_date = getattr(msg, 'date', None)

    # 1. HEADER-CHAIN: find existing messages whose Message-ID is an ancestor of
    #    this one. Account-wide lookup → cross-folder members thread together.
    ref_ids = _referenced_ids(msg)
    if ref_ids:
        parents = list(
            MailMessage.objects
            .filter(account=account, message_id__in=ref_ids, thread__isnull=False)
            .select_related('thread')
        )
        threads = {p.thread_id: p.thread for p in parents}
        if threads:
            # Merged-thread case (refs span >1 existing thread): keep the oldest
            # as canonical, re-point the others into it, then delete them.
            canonical = min(threads.values(), key=lambda t: t.first_message_at or t.created_at)
            for t in threads.values():
                if t.id != canonical.id:
                    MailMessage.objects.filter(thread=t).update(thread=canonical)
                    MailThread.objects.filter(id=t.id).delete()
            _touch_thread(canonical, msg_date)
            return canonical

    # 2. FALLBACK: normalized-subject match (headers absent or ancestor unknown).
    clean = _clean_subject(getattr(msg, 'subject', ''))
    if clean:
        thread = MailThread.objects.filter(account=account, subject__iexact=clean).first()
        if thread:
            _touch_thread(thread, msg_date)
            return thread

    # Unthreadable: no known ancestor AND no subject.
    if not clean:
        return None

    # 3. New thread.
    return MailThread.objects.create(
        account=account, subject=clean,
        jmap_thread_id=f'imap-thread:{uuid.uuid4().hex[:12]}',  # unique ID for IMAP threads
        first_message_at=msg_date or timezone.now(),
        last_message_at=msg_date or timezone.now(),
    )


# ── JMAP (Stalwart) implementation ──

def _sync_jmap_folder_counts(account):
    from .models import MailFolder
    import asyncio
    client = _get_stalwart_client(account)
    if not client:
        return
    folders = asyncio.run(client.get_mailboxes())
    for f in folders:
        MailFolder.objects.filter(account=account, jmap_mailbox_id=f.get('id')).update(
            total_messages=f.get('totalEmails', 0),
            unread_messages=f.get('unreadEmails', 0),
            updated_at=timezone.now()
        )


def _sync_jmap_messages(account, folder_id=None, since=None, client=None) -> int:
    from .models import MailFolder, MailMessage, MailThread
    import asyncio
    client = client or _get_stalwart_client(account)
    if not client:
        return 0

    folders = MailFolder.objects.filter(account=account)
    if folder_id:
        folders = folders.filter(id=folder_id)

    total_new = 0
    for folder in folders:
        result = asyncio.run(client.get_emails(mailbox_id=folder.jmap_mailbox_id, limit=100))
        email_payload = result.get('emails', [])
        # StalwartJMAPClient returns the RFC list directly. Retain support for
        # the older wrapper shape so in-flight/alternate clients remain safe.
        emails = (
            email_payload.get('list', [])
            if isinstance(email_payload, dict)
            else email_payload
        )
        for e in emails:
            if MailMessage.objects.filter(jmap_email_id=e['id']).exists():
                continue
            from_list = e.get('from') or []
            from_info = from_list[0] if from_list else {}
            if isinstance(from_info, dict):
                from_address = from_info.get('email') or ''
                from_name = from_info.get('name') or ''
            else:
                from_address = str(from_info or '')
                from_name = ''
            thread = None
            if e.get('threadId'):
                thread, _ = MailThread.objects.get_or_create(
                    account=account, jmap_thread_id=e['threadId'],
                    defaults={'subject': e.get('subject') or '',
                              'first_message_at': e.get('receivedAt'),
                              'last_message_at': e.get('receivedAt')}
                )
            # MAIL-19: extract body_text from JMAP textBody (plain-text parts first).
            # Primary: textBody lists part-IDs for text/plain parts — avoids pulling HTML
            # duplicates that occur when iterating all bodyValues on multipart/alternative.
            jmap_body_text = ''
            body_values = e.get('bodyValues') or {}
            for tb in (e.get('textBody') or []):
                part_id = tb.get('partId', '')
                if part_id and part_id in body_values:
                    jmap_body_text += body_values[part_id].get('value', '')
            if not jmap_body_text:
                # fallback: iterate all bodyValues (pre-RFC-8621 servers without textBody)
                for bv in body_values.values():
                    if isinstance(bv, dict) and bv.get('value'):
                        jmap_body_text += bv['value']
            jmap_plain = _strip_html(jmap_body_text)
            jmap_body_val, jmap_truncated = _cap_body_text(jmap_plain)

            message = MailMessage.objects.create(
                account=account, folder=folder, thread=thread,
                jmap_email_id=e['id'], jmap_blob_id=e.get('blobId') or '',
                message_id=e.get('messageId', [''])[0] if e.get('messageId') else '',
                subject=e.get('subject') or '',
                from_address=from_address,
                from_name=from_name,
                to_addresses=e.get('to') or [],
                cc_addresses=e.get('cc') or [],
                bcc_addresses=e.get('bcc') or [],
                preview=e.get('preview') or '',
                size_bytes=e.get('size') or 0,
                body_text=jmap_body_val or None,
                body_text_truncated=jmap_truncated,
                is_unread='$seen' not in (e.get('keywords') or {}),
                is_flagged='$flagged' in (e.get('keywords') or {}),
                has_attachments=bool(e.get('hasAttachment')),
                attachment_count=len(e.get('attachments') or []),
                date_sent=e.get('sentAt'), date_received=e.get('receivedAt'),
            )
            total_new += 1

            # MAIL-55: evaluate the account's filters on newly ingested inbox
            # messages (filters were previously stored but never applied).
            if folder.role == 'inbox':
                try:
                    from .services.filter_engine import apply_filters_to_messages
                    applied = apply_filters_to_messages(
                        account,
                        [{
                            'id': message.jmap_email_id,
                            'subject': message.subject,
                            'from_address': message.from_address,
                            'to_addresses': message.to_addresses,
                            'cc_addresses': message.cc_addresses,
                            'preview': message.preview,
                            'body_text': message.body_text or '',
                            'has_attachments': message.has_attachments,
                            'folder': folder.name,
                            'folder_role': folder.role,
                            'folder_name': folder.name,
                            'mailbox_id': folder.jmap_mailbox_id or folder.name,
                            'role': folder.role,
                        }],
                        source_mailbox_id=folder.jmap_mailbox_id,
                        client=client,
                    )
                    outcome = applied.get(message.jmap_email_id)
                    if outcome:
                        update_fields = []
                        if outcome.get('moved_to') is not None:
                            message.folder = outcome['moved_to']
                            update_fields.append('folder')
                        if outcome.get('mark_read'):
                            message.is_unread = False
                            update_fields.append('is_unread')
                        if outcome.get('flag'):
                            message.is_flagged = True
                            update_fields.append('is_flagged')
                        if update_fields:
                            message.save(update_fields=update_fields + ['updated_at'])
                except Exception as fe:
                    logger.warning('MAIL-55: filter application during sync failed: %s', safe_exception_text(fe))

    # MAIL-163: refresh contacts from message history after ingestion.
    try:
        from .services.contact_service import sync_contacts_from_messages
        sync_contacts_from_messages(account)
    except Exception as ce:
        logger.warning(
            'MAIL-163: contact sync failed for %s: %s',
            redact_sensitive(account.email_address), safe_exception_text(ce),
        )

    return total_new


# ── Daemon notification ──

def _notify_daemon_new_messages(account, new_count: int):
    """POST new message summaries to the Shizuha daemon for alert creation."""
    from .models import MailMessage

    try:
        # Get the most recent new messages
        recent = MailMessage.objects.filter(account=account).order_by('-date_received')[:new_count]
        messages = []
        for m in recent:
            messages.append({
                'message_id': m.message_id,
                'imap_uid': m.imap_uid,
                'from_address': m.from_address,
                'from_name': m.from_name,
                'to': str(m.to_addresses),
                'subject': m.subject,
                'date': m.date_received.isoformat() if m.date_received else '',
                'has_attachments': m.has_attachments,
                'size': m.size_bytes,
                'preview': m.preview[:200] if m.preview else '',
            })

        resp = requests.post(
            DAEMON_WEBHOOK_URL,
            json={
                'type': 'new_messages',
                'account': account.email_address,
                'messages': messages,
            },
            timeout=5,
        )
        logger.info('Notified daemon: %s new messages for %s (status=%s)', new_count, redact_sensitive(account.email_address), resp.status_code)
    except Exception as e:
        logger.warning('Failed to notify daemon about new messages: %s', safe_exception_text(e))


# ── Housekeeping ──

@shared_task
def cleanup_old_drafts(days: int = 30):
    from .models import MailDraft
    from datetime import timedelta
    cutoff = timezone.now() - timedelta(days=days)
    deleted_count, _ = MailDraft.objects.filter(updated_at__lt=cutoff).delete()
    logger.info(f'Deleted {deleted_count} old drafts')
    return deleted_count


@shared_task
def process_incoming_webhook(payload: dict):
    event_type = payload.get('type')
    account_email = payload.get('account')
    if not event_type or not account_email:
        logger.warning('Invalid webhook payload: %s', redact_sensitive(payload))
        return

    from .models import MailAccount
    try:
        account = MailAccount.objects.get(email_address=account_email)
        if _is_managed_identity(account):
            logger.info(
                'Skipping webhook background sync for managed account_id=%s',
                account.id,
            )
            return
        if event_type == 'new_message':
            sync_new_messages.delay(str(account.id), expected_user_id=account.user_id)
        elif event_type == 'mailbox_update':
            sync_mailbox_counts.delay(str(account.id), expected_user_id=account.user_id)
        logger.info('Processed webhook %s for %s', redact_sensitive(event_type), redact_sensitive(account_email))
    except MailAccount.DoesNotExist:
        logger.warning('Webhook for unknown account: %s', redact_sensitive(account_email))


# ── MAIL-34: Periodic sync ────────────────────────────────────────────────────

_NON_CONNECTABLE_STATUSES = PERSISTENT_STATUSES | {'needs_attention'}
_PERIODIC_SYNC_DEDUP_SECS = 60


@shared_task
def periodic_sync_all_accounts():
    """Fan-out sync for all connectable accounts; skips recently-synced (< 60s)."""
    from .models import MailAccount
    try:
        cutoff = timezone.now() - timedelta(seconds=_PERIODIC_SYNC_DEDUP_SECS)
        accounts = list(
            MailAccount.objects
            .exclude(settings__managed_identity=True)
            .exclude(connection_status__in=_NON_CONNECTABLE_STATUSES)
            .exclude(last_sync_at__gte=cutoff)
            .only('id', 'user_id')
        )
        for account in accounts:
            account_id = str(account.id)
            sync_mailbox_counts.delay(account_id, expected_user_id=account.user_id)
            sync_new_messages.delay(account_id, expected_user_id=account.user_id)
        logger.info('[periodic_sync] dispatched for %d accounts', len(accounts))
        return {'dispatched': len(accounts)}
    except Exception as exc:
        logger.error('[periodic_sync] failed to query accounts: %s', safe_exception_text(exc))
        return {'dispatched': 0, 'error': str(exc)}


# ── MAIL-107: managed-account reconcile cadence ──────────────────────────────

@shared_task
def reconcile_managed_mail_accounts_periodic():
    """Daily converge of managed-identity mail routing + synthetic-fixture TTL expiry.

    Wraps the reconcile_managed_mail_accounts management command (the sweep that
    expires synthetic QA-fixture accounts past their TTL and re-points managed
    identities at the unified cluster mail plane). Until this cadence existed the
    command had zero production callers — nothing fired it, so a synthetic
    fixture ~50 days past its 7-day TTL stayed active in Zen's production
    selector (MAIL-107 regression; sweep itself shipped green in mail#40 and was
    regression-verified via the PLAT-5467 port).
    """
    from django.core.management import call_command

    try:
        out = io.StringIO()
        call_command('reconcile_managed_mail_accounts', stdout=out)
        lines = out.getvalue().strip().splitlines()
        summary = lines[-1] if lines else 'no output'
        logger.info('[managed_reconcile] %s', summary)
        return {'summary': summary}
    except Exception as exc:
        logger.error('[managed_reconcile] failed: %s', safe_exception_text(exc))
        return {'error': str(exc)}

# ── MAIL-32: body_text backfill ──────────────────────────────────────────────

def _extract_jmap_body(email_data: dict) -> str:
    """Extract plain-text body from a JMAP Email/get response (same logic as sync path)."""
    body_values = email_data.get('bodyValues') or {}
    text = ''
    for tb in (email_data.get('textBody') or []):
        part_id = tb.get('partId', '')
        if part_id and part_id in body_values:
            text += body_values[part_id].get('value', '')
    if not text:
        for bv in body_values.values():
            if isinstance(bv, dict) and bv.get('value'):
                text += bv['value']
    return text


@shared_task(bind=True)
def backfill_message_body_text(self):
    """One-time backfill: populate body_text for MailMessage rows that predate
    the MAIL-19 trigger.  Idempotent — processes only rows with body_text IS NULL.

    Safe to invoke via: backfill_message_body_text.delay()
    or: python manage.py backfill_mail_body_text
    """
    from django.conf import settings
    from .models import MailMessage

    batch_size = getattr(settings, 'MAIL_BACKFILL_BATCH_SIZE', 100)
    sleep_between_batches = 0.1

    total = MailMessage.objects.filter(body_text__isnull=True).count()
    processed = 0
    errors = 0
    last_pk = 0

    logger.info('[backfill] starting body_text backfill — %d rows to process', total)

    while True:
        batch = list(
            MailMessage.objects
            .filter(body_text__isnull=True, pk__gt=last_pk)
            .select_related('account', 'folder')
            .order_by('pk')[:batch_size]
        )
        if not batch:
            break

        for msg in batch:
            last_pk = msg.pk
            try:
                raw_body = _fetch_body_for_message(msg)
                if raw_body is None:
                    # fetch returned nothing — skip, leave body_text NULL for retry
                    continue
                plain = _strip_html(raw_body)
                capped, truncated = _cap_body_text(plain)
                MailMessage.objects.filter(pk=msg.pk, body_text__isnull=True).update(
                    body_text=capped or '',
                    body_text_truncated=truncated,
                )
                processed += 1
            except Exception as exc:
                errors += 1
                logger.warning(
                    '[backfill] skipping message pk=%d account=%s: %s',
                    msg.pk,
                    redact_sensitive(str(msg.account_id)),
                    safe_exception_text(exc),
                )

        self.update_state(state='PROGRESS', meta={'processed': processed, 'total': total, 'errors': errors})
        time.sleep(sleep_between_batches)

    logger.info('[backfill] done — processed=%d errors=%d total=%d', processed, errors, total)
    return {'processed': processed, 'errors': errors, 'total': total}


SEARCH_VECTOR_SQL = r'''
    setweight(to_tsvector('simple', coalesce(subject, '')), 'A') ||
    setweight(to_tsvector('simple', coalesce(from_address, '') || ' ' || coalesce(from_name, '')), 'B') ||
    setweight(to_tsvector('simple', coalesce(to_addresses::text, '') || ' ' || coalesce(cc_addresses::text, '')), 'B') ||
    setweight(to_tsvector('simple', coalesce(preview, '')), 'C')
'''


@shared_task(bind=True)
def backfill_message_search_vector(self):
    """Backfill search_vector for MailMessage rows where it is NULL (PLAT-6826).

    The MAIL-17 trigger populates search_vector on INSERT/UPDATE of the indexed
    columns, but rows written before the trigger existed (or via paths that
    bypassed it) carry NULL search_vector and are invisible to FTS search.
    Idempotent — processes only rows with search_vector IS NULL, mirroring the
    trigger's exact tsvector expression via raw SQL (a direct UPDATE of
    search_vector does not re-fire the BEFORE UPDATE OF <indexed cols> trigger).

    Safe to invoke via: backfill_message_search_vector.delay()
    or: python manage.py backfill_mail_search_vector
    """
    from django.conf import settings
    from django.db import connection, transaction
    from .models import MailMessage

    batch_size = getattr(settings, 'MAIL_BACKFILL_BATCH_SIZE', 100)
    sleep_between_batches = 0.1

    if connection.vendor != 'postgresql':
        logger.info('[backfill] search_vector backfill skipped — non-Postgres backend')
        return {'processed': 0, 'errors': 0, 'total': 0}

    total = MailMessage.objects.filter(search_vector__isnull=True).count()
    processed = 0
    errors = 0
    last_pk = None

    logger.info('[backfill] starting search_vector backfill — %d rows to process', total)

    while True:
        pending = MailMessage.objects.filter(search_vector__isnull=True)
        if last_pk is not None:
            pending = pending.filter(pk__gt=last_pk)
        batch = list(pending.order_by('pk').values_list('pk', flat=True)[:batch_size])
        if not batch:
            break

        last_pk = batch[-1]
        try:
            with transaction.atomic(), connection.cursor() as cursor:
                cursor.execute(
                    f'UPDATE mail_message SET search_vector = {SEARCH_VECTOR_SQL} '
                    f'WHERE {connection.ops.quote_name(MailMessage._meta.pk.column)} = ANY(%s) AND search_vector IS NULL',
                    [list(batch)],
                )
            processed += cursor.rowcount
        except Exception as exc:
            errors += 1
            logger.warning(
                '[backfill] search_vector batch failed (pks=%s..%s): %s',
                batch[0], batch[-1], safe_exception_text(exc),
            )

        self.update_state(state='PROGRESS', meta={'processed': processed, 'total': total, 'errors': errors})
        time.sleep(sleep_between_batches)

    logger.info('[backfill] search_vector done — processed=%d errors=%d total=%d', processed, errors, total)
    return {'processed': processed, 'errors': errors, 'total': total}


def _fetch_body_for_message(msg) -> str | None:
    """Fetch raw body text for a single MailMessage via the appropriate protocol."""
    account = msg.account
    if _is_jmap_account(account):
        if not msg.jmap_email_id:
            return None
        client = _get_stalwart_client(account)
        if not client:
            return None
        email_data = asyncio.run(client.get_email(msg.jmap_email_id, include_body=True))
        if not email_data:
            return None
        return _extract_jmap_body(email_data)
    else:
        # IMAP
        if not msg.imap_uid or not msg.folder:
            return None
        imap = _get_imap_client(account)
        with imap:
            result = imap.fetch_message(msg.folder.name, msg.imap_uid, include_body=True)
        raw = result.get('body_text') or ''
        if not raw:
            raw = result.get('body_html') or ''
        return raw
