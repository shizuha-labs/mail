"""
MAIL-55 + MAIL-64 (MAIL-4 O2): backend mail filter evaluation, application, preview.

MAIL-55 introduced backend evaluation/application of filters (previously filters
were stored but never evaluated, so rules silently did nothing). MAIL-64 turns it
into a deterministic rule engine with a non-mutating dry-run preview:

- Typed conditions: sender, recipient, subject, body, folder, has-attachment.
- Typed actions: move, label, flag, archive, trash, mark-read.
- Deterministic rule ordering (priority, name, id) + precedence-based conflict
  resolution, so applying the same rule set repeatedly is IDEMPOTENT.
- ``resolve_message_plan`` is the single PURE planner shared by both preview and
  apply, so the dry-run preview can never disagree with the real application.

Called from BOTH ingestion paths (live inbox listing + periodic sync task) plus
the MAIL-64 preview endpoint. Canonical contract: wiki "HLD: Mail Filter Rule
Engine (MAIL-64)".
"""
import asyncio
import logging

from mail.redaction import redact_sensitive, safe_exception_text

logger = logging.getLogger(__name__)

# Text fields matched by the substring/equality operators. The typed O2 names
# `sender`/`recipient` are aliased onto the normalized message keys `from`/`to`.
_TEXT_FIELDS = ('subject', 'from', 'to', 'cc', 'body')
_FIELD_ALIASES = {
    'sender': 'from', 'from_address': 'from', 'fromaddress': 'from',
    'recipient': 'to', 'to_address': 'to', 'toaddress': 'to', 'recipients': 'to',
}
_ATTACHMENT_FIELDS = ('has_attachment', 'has_attachments', 'has-attachment', 'hasattachments', 'attachment', 'attachments')


def _condition_field(condition):
    raw = str(condition.get('field') or condition.get('property') or '').strip().lower()
    return _FIELD_ALIASES.get(raw, raw)


def _condition_operator(condition):
    return str(condition.get('operator') or condition.get('op') or 'contains').strip().lower()


def _condition_value(condition):
    """Return the raw condition value, preserving an explicit boolean False.

    ``condition.get('value') or ...`` would collapse ``value: false`` into
    ``''``, so the has-attachment branch could never distinguish an explicit
    "no attachments" rule from an omitted value. Return the value verbatim
    (callers coerce to str where needed).
    """
    value = condition.get('value')
    if value is None:
        value = condition.get('term')
    return value if value is not None else ''


def _address_text(value):
    """Flatten JMAP/legacy address shapes to searchable text."""
    if not value:
        return ''
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return ' '.join(str(v) for v in (value.get('email'), value.get('name')) if v)
    if isinstance(value, (list, tuple)):
        return ' '.join(_address_text(item) for item in value)
    return str(value)


def _field_text(message, field):
    """Extract the searchable text of a text field from a normalized message dict."""
    if field == 'subject':
        return str(message.get('subject') or '')
    if field == 'from':
        return _address_text(message.get('from_address') or message.get('from'))
    if field == 'to':
        return _address_text(message.get('to_addresses') or message.get('to'))
    if field == 'cc':
        return _address_text(message.get('cc_addresses') or message.get('cc'))
    if field == 'body':
        return str(message.get('body_text') or message.get('preview') or '')
    return ''


def _folder_text(message):
    """The message's current folder/mailbox, for the `folder` condition."""
    for key in ('folder', 'folder_name', 'folder_role', 'mailbox', 'mailbox_name', 'role'):
        val = message.get(key)
        if val:
            return str(val)
    return ''


def _has_attachment(message):
    """Boolean attachment presence, tolerant of the normalized-dict shapes."""
    for key in ('has_attachments', 'has_attachment', 'hasAttachment'):
        if key in message and message.get(key) is not None:
            return bool(message.get(key))
    atts = message.get('attachments')
    if isinstance(atts, (list, tuple)):
        return len(atts) > 0
    count = message.get('attachment_count')
    if isinstance(count, int):
        return count > 0
    return False


def _truthy(raw):
    return str(raw).strip().lower() in ('true', '1', 'yes', 'y', 'on', 'has', 'present')


def _matches(operator, haystack, needle):
    haystack = (haystack or '').lower()
    needle = '' if needle is None else str(needle).lower()
    if operator in ('contains', 'includes'):
        return needle in haystack
    if operator in ('not_contains', 'does_not_contain'):
        return needle not in haystack
    if operator in ('equals', 'is'):
        return haystack == needle
    if operator in ('not_equals', 'is_not'):
        return haystack != needle
    if operator in ('starts_with', 'startswith'):
        return haystack.startswith(needle)
    if operator in ('ends_with', 'endswith'):
        return haystack.endswith(needle)
    # Unknown operator: fail closed (no match) rather than mis-firing actions.
    return False


def _condition_result(condition, message):
    field = _condition_field(condition)
    operator = _condition_operator(condition)
    value = _condition_value(condition)
    if field in _ATTACHMENT_FIELDS:
        # `has-attachment` is boolean: value (if given) is the wanted state,
        # default wanted=True; `is_not`/`not_contains` inverts. An explicit
        # boolean False must be honored (not collapsed to the True default).
        if isinstance(value, bool):
            want = value
        elif value:
            want = _truthy(value)
        else:
            want = True
        got = _has_attachment(message)
        if operator in ('not', 'is_not', 'not_equals', 'not_contains', 'does_not_contain'):
            return got != want
        return got == want
    if field == 'folder':
        return _matches(operator, _folder_text(message), value)
    if field in _TEXT_FIELDS:
        return _matches(operator, _field_text(message, field), value)
    # Unknown/unsupported field: fail closed.
    return False


def message_matches(mail_filter, message):
    """True if the message satisfies the filter's conditions per its match_type."""
    conditions = [c for c in (mail_filter.conditions or []) if isinstance(c, dict)]
    if not conditions:
        return False
    results = [_condition_result(c, message) for c in conditions]
    return all(results) if mail_filter.match_type == 'all' else any(results)


def _action_type(action):
    return str(action.get('type') or action.get('action') or '').strip().lower()


def _action_labels(action):
    out = []
    for key in ('label', 'labels', 'value', 'tag', 'tags'):
        val = action.get(key)
        if isinstance(val, (list, tuple)):
            out.extend(str(x).strip() for x in val if str(x).strip())
        elif val:
            out.append(str(val).strip())
    return out


def plan_actions(mail_filter):
    """Normalize ONE filter's action list into a plan dict (pure, no I/O)."""
    plan = {
        'move_folder_ref': None, 'archive': False, 'delete': False,
        'mark_read': False, 'flag': False, 'labels': [],
    }
    for action in (mail_filter.actions or []):
        if not isinstance(action, dict):
            continue
        kind = _action_type(action)
        if kind in ('move', 'move_to_folder', 'fileinto', 'copy'):
            plan['move_folder_ref'] = (
                action.get('folder_id') or action.get('folder') or action.get('target')
            )
        elif kind in ('mark_read', 'mark_as_read', 'read'):
            plan['mark_read'] = True
        elif kind in ('flag', 'star', 'mark_flagged'):
            plan['flag'] = True
        elif kind in ('archive',):
            plan['archive'] = True
        elif kind in ('delete', 'trash'):
            plan['delete'] = True
        elif kind in ('label', 'add_label', 'tag'):
            for label in _action_labels(action):
                if label not in plan['labels']:
                    plan['labels'].append(label)
    return plan


# Destination precedence for conflict resolution. A message has exactly ONE
# destination folder, so when several non-stop rules match, trash beats archive
# beats move — a deterministic outcome regardless of the order they matched in.
_DEST_PRECEDENCE = {'move': 1, 'archive': 2, 'trash': 3}


def resolve_message_plan(filters, message):
    """PURE, deterministic resolution of the effective plan for ONE message.

    Shared by ``preview_filters_for_messages`` and ``apply_filters_to_messages`` so
    the dry-run preview and the real application can never disagree (MAIL-64 AC).
    ``filters`` MUST already be ordered (priority, name, id). Returns::

        {'matched': [MailFilter, ...],           # rules that matched, in order
         'destination': ('move'|'archive'|'trash', folder_ref|None) | None,
         'mark_read': bool, 'flag': bool, 'labels': [str, ...]}

    Idempotent: the destination is precedence-resolved, mark_read/flag are boolean
    OR, and labels are a de-duplicated union — re-running yields an identical plan.
    """
    matched = []
    destination = None
    mark_read = False
    flag = False
    labels = []
    for mail_filter in filters:
        if not message_matches(mail_filter, message):
            continue
        matched.append(mail_filter)
        plan = plan_actions(mail_filter)
        if plan['delete']:
            candidate = ('trash', None)
        elif plan['archive']:
            candidate = ('archive', None)
        elif plan['move_folder_ref']:
            candidate = ('move', plan['move_folder_ref'])
        else:
            candidate = None
        if candidate is not None and (
            destination is None
            or _DEST_PRECEDENCE[candidate[0]] > _DEST_PRECEDENCE[destination[0]]
        ):
            destination = candidate
        mark_read = mark_read or plan['mark_read']
        flag = flag or plan['flag']
        for label in plan['labels']:
            if label not in labels:
                labels.append(label)
        if mail_filter.stop_processing:
            break
    return {
        'matched': matched,
        'destination': destination,
        'mark_read': mark_read,
        'flag': flag,
        'labels': sorted(labels),
    }


def _active_filters(account):
    """Active filters in the canonical deterministic order (priority, name, id)."""
    from mail.models import MailFilter
    return list(
        MailFilter.objects
        .filter(account=account, is_active=True)
        .order_by('priority', 'name', 'id')
    )


def _lookup_folder(account, folder_ref):
    """Resolve a folder ref (UUID or name) to a MailFolder without side effects."""
    from mail.models import MailFolder
    if not folder_ref:
        return None
    folder = None
    try:
        folder = MailFolder.objects.filter(account=account, id=folder_ref).first()
    except Exception:  # not a valid UUID — fall through to name lookup
        folder = None
    if folder is None:
        folder = MailFolder.objects.filter(account=account, name=str(folder_ref)).first()
    return folder


def _role_folder(account, role):
    from mail.models import MailFolder
    return (
        MailFolder.objects
        .filter(account=account, role=role)
        .exclude(jmap_mailbox_id='')
        .first()
    )


def _trash_folder(account):
    return _role_folder(account, 'trash')


def _resolve_target_folder(account, folder_ref, client=None):
    """Resolve a filter action's folder reference to a MailFolder with a JMAP id.

    Accepts a MailFolder UUID or a folder name. Self-heals folders created before
    MAIL-55 (local row exists but no Stalwart mailbox) by creating the mailbox now
    and persisting its id.
    """
    folder = _lookup_folder(account, folder_ref)
    if folder is None:
        return None
    if not folder.jmap_mailbox_id and client is not None:
        try:
            created = asyncio.run(client.create_mailbox(folder.name))
            folder.jmap_mailbox_id = created.get('id') or ''
            if folder.jmap_mailbox_id:
                folder.save(update_fields=['jmap_mailbox_id', 'updated_at'])
        except Exception as e:
            logger.warning(
                'MAIL-55: could not create Stalwart mailbox for folder %s: %s',
                redact_sensitive(str(folder.id)), safe_exception_text(e),
            )
            return None
    return folder if folder.jmap_mailbox_id else None


def preview_filters_for_messages(account, messages):
    """MAIL-64 dry-run: compute what the active filters WOULD do — NO mutation.

    Returns a list of per-message preview rows (only for messages some filter
    matched)::

        {'email_id', 'subject', 'from',
         'matched_filters': [name, ...],
         'actions': {'destination': 'move'|'archive'|'trash'|None,
                     'move_to': {'ref','name','id'}|None,
                     'mark_read': bool, 'flag': bool, 'labels': [str, ...]}}

    Uses the same ``resolve_message_plan`` as apply, so preview == apply.
    """
    filters = _active_filters(account)
    preview = []
    if not filters:
        return preview
    for message in messages:
        if not isinstance(message, dict):
            continue
        email_id = message.get('id') or message.get('jmap_email_id')
        if not email_id:
            continue
        plan = resolve_message_plan(filters, message)
        if not plan['matched']:
            continue
        destination = plan['destination']
        dest_kind = destination[0] if destination else None
        move_to = None
        if dest_kind == 'move':
            folder = _lookup_folder(account, destination[1])
            move_to = {
                'ref': str(destination[1]),
                'name': folder.name if folder else None,
                'id': str(folder.id) if folder else None,
            }
        preview.append({
            'email_id': email_id,
            'subject': str(message.get('subject') or ''),
            'from': _address_text(message.get('from_address') or message.get('from')),
            'matched_filters': [f.name for f in plan['matched']],
            'actions': {
                'destination': dest_kind,
                'move_to': move_to,
                'mark_read': plan['mark_read'],
                'flag': plan['flag'],
                'labels': plan['labels'],
            },
        })
    return preview


def apply_filters_to_messages(account, messages, source_mailbox_id, client):
    """Apply the account's active filters to normalized message dicts via JMAP.

    Uses the same ``resolve_message_plan`` as the preview, so the applied outcome
    matches what the dry-run reported. Each message dict needs an email id key
    ('id' or 'jmap_email_id') plus the text/attachment/folder fields conditions
    read. Returns, for messages any filter matched::

        {email_id: {'moved_to': MailFolder|None, 'mark_read': bool, 'flag': bool,
                    'labels': [str, ...], 'archived': bool}}

    (JMAP failures are logged and that entry is dropped.)
    """
    filters = _active_filters(account)
    if not filters or client is None:
        return {}

    applied = {}
    for message in messages:
        email_id = message.get('id') or message.get('jmap_email_id')
        if not email_id:
            continue
        plan = resolve_message_plan(filters, message)
        if not plan['matched']:
            continue

        moved_to = None
        archived = False
        destination = plan['destination']
        if destination is not None:
            kind, ref = destination
            if kind == 'trash':
                moved_to = _trash_folder(account)
            elif kind == 'archive':
                moved_to = _role_folder(account, 'archive')
                archived = moved_to is not None
            elif kind == 'move':
                moved_to = _resolve_target_folder(account, ref, client)

        outcome = {
            'moved_to': moved_to,
            'mark_read': plan['mark_read'],
            'flag': plan['flag'],
            'labels': plan['labels'],
            'archived': archived,
        }
        try:
            if moved_to is not None:
                asyncio.run(client.move_emails(
                    [email_id], moved_to.jmap_mailbox_id, source_mailbox_id,
                ))
            if outcome['mark_read'] or outcome['flag'] or plan['labels']:
                asyncio.run(client.set_email_keywords(
                    [email_id],
                    is_unread=False if outcome['mark_read'] else None,
                    is_flagged=True if outcome['flag'] else None,
                    labels=plan['labels'] or None,
                ))
        except Exception as e:
            logger.warning(
                'MAIL-55/64: filter action failed for message %s on account %s: %s',
                redact_sensitive(str(email_id)), redact_sensitive(str(account.id)),
                safe_exception_text(e),
            )
            continue
        applied[email_id] = outcome
    return applied
