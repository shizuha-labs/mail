"""MAIL-163: contact quality — dedup, autocomplete ranking, sync from message history.

Contacts are populated/refreshed from the account's message history (senders
and recipients), deduplicated deterministically by (account, email), and ranked
by recency/frequency for autocomplete. All operations are idempotent: re-running
converges to the same contact set.
"""

from __future__ import annotations

import logging

from django.db import transaction
from django.db.models import Count, Max
from django.utils import timezone

from ..models import MailContact, MailMessage

logger = logging.getLogger(__name__)


def canonical_email(email) -> str:
    """Normalize an email address for deterministic dedup (lowercase, trimmed)."""
    if not email:
        return ""
    return str(email).strip().lower()


def _merge_duplicate_contacts(account) -> int:
    """Merge duplicate (account, email) contacts deterministically.

    Rule (documented): keep the row with the highest (send_count + receive_count);
    tie-break by most recent last_contacted_at, then oldest created_at as the
    canonical row. Usage signals are summed across duplicates so no history is
    lost. Returns the number of duplicate rows removed.
    """
    dupes = (
        MailContact.objects.filter(account=account)
        .values("email")
        .annotate(n=Count("id"))
        .filter(n__gt=1)
    )
    removed = 0
    for row in dupes:
        email = row["email"]
        rows = list(
            MailContact.objects.filter(account=account, email=email).order_by(
                "-send_count", "-receive_count", "-last_contacted_at", "created_at"
            )
        )
        if len(rows) <= 1:
            continue
        keep = rows[0]
        total_send = sum(r.send_count for r in rows)
        total_receive = sum(r.receive_count for r in rows)
        last = max(
            (r.last_contacted_at for r in rows if r.last_contacted_at), default=None
        )
        name = next((r.name for r in rows if r.name), "")
        with transaction.atomic():
            keep.send_count = total_send
            keep.receive_count = total_receive
            keep.last_contacted_at = last
            if name:
                keep.name = name
            keep.save(update_fields=["send_count", "receive_count", "last_contacted_at", "name", "updated_at"])
            removed += MailContact.objects.filter(
                account=account, email=email
            ).exclude(pk=keep.pk).delete()[0]
    return removed


def sync_contacts_from_messages(account, limit: int = 1000) -> int:
    """Populate/refresh contacts from the account's message history.

    Senders (from_address) increment receive_count; recipients (to_addresses)
    increment send_count. last_contacted_at tracks the most recent message.
    Idempotent: re-running converges to the same contact set.
    Returns the number of contacts upserted.
    """
    if account is None:
        return 0
    now = timezone.now()
    upserted = 0

    # Senders: we received from these addresses.
    sender_rows = (
        MailMessage.objects.filter(account=account)
        .exclude(from_address="")
        .values("from_address", "from_name")
        .annotate(cnt=Count("id"), last=Max("created_at"))
    )
    for row in sender_rows:
        email = canonical_email(row["from_address"])
        if not email:
            continue
        _, created = MailContact.objects.update_or_create(
            account=account,
            email=email,
            defaults={
                "name": row["from_name"] or "",
                "receive_count": row["cnt"],
                "last_contacted_at": row["last"] or now,
            },
        )
        upserted += 1

    # Recipients: we sent to these addresses (to_addresses is a JSON list).
    recipient_counts: dict[str, int] = {}
    recipient_last: dict[str, object] = {}
    recipient_name: dict[str, str] = {}
    for msg in (
        MailMessage.objects.filter(account=account)
        .exclude(to_addresses=[])
        .only("to_addresses", "created_at")
        .order_by("-created_at")[:limit]
    ):
        for addr in msg.to_addresses or []:
            email = canonical_email(addr)
            if not email:
                continue
            recipient_counts[email] = recipient_counts.get(email, 0) + 1
            if recipient_last.get(email) is None or (
                msg.created_at and msg.created_at > recipient_last[email]
            ):
                recipient_last[email] = msg.created_at
            recipient_name.setdefault(email, "")
    for email, cnt in recipient_counts.items():
        _, created = MailContact.objects.update_or_create(
            account=account,
            email=email,
            defaults={
                "name": recipient_name.get(email, ""),
                "send_count": cnt,
                "last_contacted_at": recipient_last.get(email) or now,
            },
        )
        upserted += 1

    return upserted
