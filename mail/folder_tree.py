"""Account-scoped folder tree helpers for Mail.

Mail folders are mailbox metadata cached per account.  The helpers here keep the
REST/MCP-facing tree deterministic and make the account boundary explicit so a
folder from one account can never be nested into or counted in another account's
tree.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from django.db.models import Count, Q, Sum

from .models import MailAccount, MailFolder, MailMessage

SPECIAL_FOLDER_ROLES = {"inbox", "drafts", "sent", "trash", "junk", "archive"}
SPECIAL_ROLE_ORDER = {
    "inbox": 0,
    "drafts": 10,
    "sent": 20,
    "archive": 30,
    "junk": 40,
    "trash": 50,
    "custom": 100,
}


def normalize_folder_role(role: str | None) -> str:
    role = (role or "custom").lower()
    return role if role in SPECIAL_ROLE_ORDER else "custom"


def is_special_folder(folder: MailFolder) -> bool:
    return normalize_folder_role(folder.role) in SPECIAL_FOLDER_ROLES


def server_folder_ref(folder: MailFolder) -> str:
    """Return the provider identifier for a local folder."""
    if folder.account.incoming_server_type == "imap":
        return folder.name
    return folder.jmap_mailbox_id or folder.name


def assert_custom_folder(folder: MailFolder, action: str) -> None:
    """Reject destructive/renaming operations on provider special folders."""
    if is_special_folder(folder):
        raise ValueError(f"Cannot {action} special folder '{folder.name}' ({folder.role}).")


def sibling_name_exists(
    account: MailAccount,
    name: str,
    parent: MailFolder | None,
    *,
    exclude: MailFolder | None = None,
) -> bool:
    qs = MailFolder.objects.filter(account=account, parent=parent, name__iexact=name.strip())
    if exclude is not None:
        qs = qs.exclude(pk=exclude.pk)
    return qs.exists()


def _folder_sort_key(folder: MailFolder):
    return (
        SPECIAL_ROLE_ORDER.get(normalize_folder_role(folder.role), SPECIAL_ROLE_ORDER["custom"]),
        folder.sort_order,
        (folder.name or "").lower(),
        str(folder.id),
    )


def _message_counts(account: MailAccount) -> dict:
    counts = {}
    rows = (
        MailMessage.objects
        .filter(account=account)
        .values("folder_id")
        .annotate(
            total=Count("id"),
            unread=Count("id", filter=Q(is_unread=True)),
            size=Sum("size_bytes"),
        )
    )
    for row in rows:
        counts[row["folder_id"]] = {
            "total_messages": row["total"] or 0,
            "unread_messages": row["unread"] or 0,
            "total_size_bytes": row["size"] or 0,
        }
    return counts


def refresh_folder_counts_from_messages(account: MailAccount, folder_ids: Iterable) -> None:
    """Refresh cached counters for folders whose local message membership changed."""
    folder_ids = {fid for fid in folder_ids if fid}
    if not folder_ids:
        return
    counts = _message_counts(account)
    for folder in MailFolder.objects.filter(account=account, id__in=folder_ids):
        values = counts.get(folder.id, {"total_messages": 0, "unread_messages": 0, "total_size_bytes": 0})
        MailFolder.objects.filter(pk=folder.pk).update(**values)


def serialize_folder_tree(account: MailAccount) -> list[dict]:
    """Return a deterministic, account-scoped folder tree with safe metadata."""
    folders = list(MailFolder.objects.filter(account=account).select_related("parent"))
    counts = _message_counts(account)
    by_id = {folder.id: folder for folder in folders}
    children = defaultdict(list)
    roots = []

    for folder in folders:
        parent_id = folder.parent_id
        # Defensive account boundary: a corrupt/stale parent reference outside
        # this account must not attach the folder to another account's tree.
        if parent_id and parent_id in by_id:
            children[parent_id].append(folder)
        else:
            roots.append(folder)

    for child_list in children.values():
        child_list.sort(key=_folder_sort_key)
    roots.sort(key=_folder_sort_key)

    def node(folder: MailFolder) -> dict:
        local_counts = counts.get(folder.id)
        total_messages = local_counts["total_messages"] if local_counts is not None else folder.total_messages
        unread_messages = local_counts["unread_messages"] if local_counts is not None else folder.unread_messages
        total_size_bytes = local_counts["total_size_bytes"] if local_counts is not None else folder.total_size_bytes
        special = is_special_folder(folder)
        folder_children = children.get(folder.id, [])
        return {
            "id": str(folder.id),
            "account_id": str(account.id),
            "jmap_mailbox_id": folder.jmap_mailbox_id,
            "name": folder.name,
            "role": normalize_folder_role(folder.role),
            "special_role": normalize_folder_role(folder.role) if special else None,
            "is_special": special,
            "can_rename": not special,
            "can_delete": not special and not folder_children and total_messages == 0,
            "parent": str(folder.parent_id) if folder.parent_id else None,
            "sort_order": folder.sort_order,
            "total_messages": total_messages,
            "unread_messages": unread_messages,
            "total_size_bytes": total_size_bytes,
            "has_children": bool(folder_children),
            "children": [node(child) for child in folder_children],
            "created_at": folder.created_at.isoformat() if folder.created_at else None,
            "updated_at": folder.updated_at.isoformat() if folder.updated_at else None,
        }

    return [node(folder) for folder in roots]


def reconcile_provider_folders(account: MailAccount, provider_folders: Iterable[dict]) -> list[MailFolder]:
    """Persist provider folder metadata into the account-local cache.

    The provider returns parent links by server mailbox id.  We upsert all rows
    first, then wire parent pointers inside this account only.
    """
    provider_folders = list(provider_folders or [])
    server_to_folder: dict[str, MailFolder] = {}
    parent_by_server_id: dict[str, str | None] = {}

    for item in provider_folders:
        name = (item.get("name") or "").strip()
        if not name:
            continue
        server_id = item.get("jmap_mailbox_id") or item.get("id") or f"imap:{name}"
        role = normalize_folder_role(item.get("role"))
        defaults = {
            "name": name,
            "role": role,
            "sort_order": int(item.get("sort_order") or item.get("sortOrder") or 0),
            "total_messages": int(item.get("total_messages") or item.get("totalEmails") or 0),
            "unread_messages": int(item.get("unread_messages") or item.get("unreadEmails") or 0),
            "total_size_bytes": int(item.get("total_size_bytes") or 0),
        }
        folder, _ = MailFolder.objects.update_or_create(
            account=account,
            jmap_mailbox_id=str(server_id),
            defaults=defaults,
        )
        server_to_folder[str(server_id)] = folder
        parent_by_server_id[str(server_id)] = item.get("parent_id") or item.get("parentId")

    for server_id, parent_server_id in parent_by_server_id.items():
        folder = server_to_folder[server_id]
        parent = server_to_folder.get(str(parent_server_id)) if parent_server_id else None
        if folder.parent_id != (parent.id if parent else None):
            folder.parent = parent
            folder.save(update_fields=["parent", "updated_at"])

    return list(server_to_folder.values())
