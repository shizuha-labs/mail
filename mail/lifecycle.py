"""Signed, idempotent Shizuha ID lifecycle consumer for unified mailboxes."""

import hashlib
import hmac
import json
import time
from urllib.parse import urlparse

from django.conf import settings
from django.db import models, transaction
from django.utils import timezone

from .models import MailAccount, UserLifecycleEventReceipt
from .services.stalwart_client import StalwartManagementClient


ACTIVE_EVENTS = {'user.activated', 'user.reactivated'}
INACTIVE_EVENTS = {'user.deactivated', 'user.deleted'}
SUPPORTED_EVENTS = ACTIVE_EVENTS | INACTIVE_EVENTS


class LifecycleAuthenticationError(ValueError):
    pass


class LifecyclePayloadError(ValueError):
    pass


def verify_signed_payload(*, body, headers):
    secret = settings.ID_TO_MAIL_LIFECYCLE_SECRET
    if not secret:
        raise LifecycleAuthenticationError('lifecycle secret is not configured')
    if headers.get('X-Shizuha-Service') != 'shizuha-id':
        raise LifecycleAuthenticationError('unexpected lifecycle publisher')
    timestamp = headers.get('X-Shizuha-Timestamp', '')
    signature = headers.get('X-Shizuha-Signature', '')
    try:
        timestamp_value = int(timestamp)
    except (TypeError, ValueError) as exc:
        raise LifecycleAuthenticationError('invalid lifecycle timestamp') from exc
    if abs(int(time.time()) - timestamp_value) > settings.USER_LIFECYCLE_SIGNATURE_MAX_AGE_SECONDS:
        raise LifecycleAuthenticationError('expired lifecycle signature')
    expected = hmac.new(
        secret.encode('utf-8'), timestamp.encode('utf-8') + b'.' + body, hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(signature, f'sha256={expected}'):
        raise LifecycleAuthenticationError('invalid lifecycle signature')
    try:
        return json.loads(body.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LifecyclePayloadError('invalid lifecycle JSON') from exc


def _mailbox_email(payload):
    username = str(payload.get('username') or '').strip().lower()
    mailbox = str(payload.get('mailbox_email') or '').strip().lower()
    if not mailbox and username:
        mailbox = f'{username}@{settings.MAIL_DOMAIN.lower()}'
    if mailbox.count('@') != 1 or mailbox.rsplit('@', 1)[1] != settings.MAIL_DOMAIN.lower():
        raise LifecyclePayloadError('mailbox must belong to the managed mail domain')
    return mailbox


def unified_account_routing():
    """Return the canonical cluster routing fields for managed ID mailboxes."""
    parsed = urlparse(getattr(
        settings, 'UNIFIED_MAIL_JMAP_URL', settings.STALWART_JMAP_URL,
    ))
    incoming_host = parsed.hostname or 'stalwart-shadow.mail-shadow.svc.cluster.local'
    incoming_port = parsed.port or (443 if parsed.scheme == 'https' else 80)
    return {
        'incoming_host': incoming_host,
        'incoming_port': incoming_port,
        'incoming_security': 'ssl' if parsed.scheme == 'https' else 'none',
        'outgoing_host': getattr(
            settings, 'UNIFIED_MAIL_SMTP_HOST',
            'stalwart-shadow-mail.mail-shadow.svc.cluster.local',
        ),
        'outgoing_port': int(getattr(settings, 'UNIFIED_MAIL_SMTP_PORT', 587)),
    }


def disable_superseded_domain_accounts(*, user_id, keep_account_id=None):
    """Preserve but deactivate non-managed rows for our authoritative domain."""
    candidate_ids = []
    candidates = MailAccount.objects.filter(
        user_id=user_id,
        is_active=True,
        email_address__iendswith=f'@{settings.MAIL_DOMAIN}',
    )
    if keep_account_id is not None:
        candidates = candidates.exclude(pk=keep_account_id)
    for candidate in candidates.only('id', 'settings').iterator():
        if not (candidate.settings or {}).get('managed_identity'):
            candidate_ids.append(candidate.id)
    if not candidate_ids:
        return 0
    return MailAccount.objects.filter(pk__in=candidate_ids).update(
        is_active=False,
        is_default=False,
        updated_at=timezone.now(),
    )


def disable_synthetic_fixture_accounts(*, max_age_days=None, now=None):
    """Deactivate synthetic QA-fixture accounts past their bounded TTL.

    MAIL-107: WIKI-27-style synthetic fixtures (``@shizuha.local`` and any
    other non-managed synthetic domain) had no teardown/expiry contract, so a
    completed QA fixture could remain ``is_active`` in a real user's profile
    forever, leaking into the account selector and all-account fan-out. The
    selectors already exclude inactive rows; this sweep is what marks them
    inactive.

    Only rows whose address ends in a configured synthetic domain suffix are
    touched (never the canonical managed ``@MAIL_DOMAIN`` account). The row is
    deactivated (not hard-deleted) so audit history is preserved.

    Returns the number of rows deactivated.
    """
    synthetic_suffixes = getattr(
        settings, 'SYNTHETIC_MAIL_DOMAIN_SUFFIXES', ('@shizuha.local',)
    )
    if not synthetic_suffixes:
        return 0
    max_age_days = int(
        max_age_days
        or getattr(settings, 'SYNTHETIC_MAIL_FIXTURE_MAX_AGE_DAYS', 7)
    )
    now = now or timezone.now()
    cutoff = now - timezone.timedelta(days=max_age_days)

    q = MailAccount.objects.filter(is_active=True)
    combined = None
    for suffix in synthetic_suffixes:
        clause = models.Q(email_address__iendswith=suffix)
        combined = clause if combined is None else (combined | clause)
    if combined is None:
        return 0
    candidates = q.filter(combined, created_at__lte=cutoff).only('id')
    ids = list(candidates.values_list('id', flat=True))
    if not ids:
        return 0
    return MailAccount.objects.filter(pk__in=ids).update(
        is_active=False,
        is_default=False,
        updated_at=now,
    )


def _upsert_local_account(*, payload, mailbox_email, stalwart_account_id):
    routing = unified_account_routing()
    display_name = ' '.join(filter(None, [payload.get('first_name'), payload.get('last_name')]))
    account_type = payload.get('account_type')
    managed_settings = {
        'managed_identity': True,
        'stalwart_account_id': stalwart_account_id,
    }
    if account_type in {'agent', 'human'}:
        managed_settings['identity_account_type'] = account_type
    account, _ = MailAccount.objects.update_or_create(
        user_id=int(payload['user_id']),
        email_address=mailbox_email,
        defaults={
            'display_name': 'Shizuha Mail',
            'from_name': display_name or payload.get('username') or mailbox_email,
            'incoming_server_type': 'jmap',
            'incoming_host': routing['incoming_host'],
            'incoming_port': routing['incoming_port'],
            'incoming_security': routing['incoming_security'],
            'incoming_username': mailbox_email,
            'encrypted_incoming_password': None,
            'auth_type': 'oauth2',
            'oauth_provider': '',
            'outgoing_host': routing['outgoing_host'],
            'outgoing_port': routing['outgoing_port'],
            'outgoing_security': 'starttls',
            'outgoing_username': mailbox_email,
            'encrypted_outgoing_password': None,
            'outgoing_requires_auth': True,
            'is_active': True,
            'connection_status': 'unknown',
            'connection_error': '',
            'settings': managed_settings,
        },
    )
    # Managed Shizuha Mail is the universal default for every approved ID
    # identity. Linked external accounts remain available as secondary
    # accounts; MailAccount.save() atomically clears their default flag.
    account.is_default = True
    account.save(update_fields=['is_default', 'updated_at'])
    # The unified ID-backed mailbox supersedes password-era client rows for
    # our own domain. Keep those rows as audit history, but never let the UI
    # contact the retired endpoint. Linked third-party accounts remain active.
    disable_superseded_domain_accounts(
        user_id=account.user_id,
        keep_account_id=account.pk,
    )
    return account


def consume_lifecycle_event(payload):
    required = {'event_id', 'event_type', 'user_id'}
    if not required.issubset(payload):
        raise LifecyclePayloadError('missing lifecycle event fields')
    event_type = payload['event_type']
    if event_type not in SUPPORTED_EVENTS:
        raise LifecyclePayloadError('unsupported lifecycle event type')
    mailbox_email = _mailbox_email(payload)

    with transaction.atomic():
        receipt, _ = UserLifecycleEventReceipt.objects.select_for_update().get_or_create(
            event_id=payload['event_id'],
            defaults={
                'event_type': event_type,
                'user_id': int(payload['user_id']),
                'mailbox_email': mailbox_email,
            },
        )
        if receipt.status == UserLifecycleEventReceipt.STATUS_PROCESSED:
            return receipt, False
        if receipt.event_type != event_type or receipt.user_id != int(payload['user_id']):
            raise LifecyclePayloadError('event id was reused with a different payload')
        if receipt.mailbox_email != mailbox_email:
            if (
                receipt.status != UserLifecycleEventReceipt.STATUS_FAILED
                or receipt.mailbox_email.count('@') == 1
            ):
                raise LifecyclePayloadError('event id was reused with a different payload')
            # Migration escape hatch for receipts written before Mail rejected
            # double-@ addresses. Only that structurally-invalid legacy form may
            # change; normal failed and all processed receipts remain immutable.
            receipt.mailbox_email = mailbox_email
        receipt.status = UserLifecycleEventReceipt.STATUS_PROCESSING
        receipt.attempts += 1
        receipt.last_error = ''
        receipt.save(update_fields=[
            'mailbox_email', 'status', 'attempts', 'last_error', 'updated_at',
        ])

    try:
        if event_type in ACTIVE_EVENTS:
            account_id, _created = StalwartManagementClient().ensure_account(
                mailbox_email,
                description=f'Shizuha ID user {payload["user_id"]}: {payload.get("username", "")}',
            )
            with transaction.atomic():
                _upsert_local_account(
                    payload=payload,
                    mailbox_email=mailbox_email,
                    stalwart_account_id=account_id,
                )
        else:
            StalwartManagementClient().delete_account(mailbox_email)
            MailAccount.objects.filter(
                user_id=int(payload['user_id']), email_address=mailbox_email,
            ).update(is_active=False, updated_at=timezone.now())
    except Exception as exc:
        UserLifecycleEventReceipt.objects.filter(pk=receipt.pk).update(
            status=UserLifecycleEventReceipt.STATUS_FAILED,
            last_error=str(exc)[:4000],
            updated_at=timezone.now(),
        )
        raise

    receipt.status = UserLifecycleEventReceipt.STATUS_PROCESSED
    receipt.processed_at = timezone.now()
    receipt.last_error = ''
    receipt.save(update_fields=['status', 'processed_at', 'last_error', 'updated_at'])
    return receipt, True
