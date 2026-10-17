#!/usr/bin/env python3
"""
Shizuha Mail MCP server — agent-facing self-scoped surface (MAIL-127 / MAIL-128).

Six-tool v1 manifest mandated by HLD §3:

  mail_account_info      → resolve the calling agent's canonical mailbox
  mail_list_messages     → list the agent's own inbox/other folders
  mail_get_message       → full content of a message the agent owns
  mail_search_messages   → single bounded search window (limit <= 50, no cursor)
  mail_send_email        → explicit send, exactly-once via client_message_id
  mail_get_send_status   → owner-scoped send/provider delivery status

Contracts enforced here (MAIL-127 §3/§5, §7, §8):
- SELF-SCOPED: no caller-supplied `account_id`/`from`/cross-owner. Every read is
  keyed to the authenticated bearer's own mailbox; the backend returns a
  non-enumerating 404 for foreign keys.
- OWNER-SCOPED 404: cross-account reads must be indistinguishable from missing.
  We never leak whether another account exists by surfacing a different error
  shape for 403/404.
- BOUNDED SEARCH: v1 `mail_search_messages` is a single window, limit <= 50,
  no offset/cursor semantics (HLD §3 P3). We clamp server-side and refuse 0.
- PLAIN-TEXT RENDERING: bodies return normalized plain text; `body_html` is
  omitted (not rendered/executed) — untrusted email content is never active.
  Headers are delimited from the body; no remote fetch, no attachment auto-decode.
- NO SECRETS/PLAINTEXT IN LOGS OR ERRORS: we never echo provider passwords,
  tokens, bodies, or snippets into logs/errors. Errors are stable, non-secret
  codes; full provider detail stays server-side.
- SEND: explicit `mail_send_email` only. exactly-once via unconditional
  `client_message_id` (REQUIRED at this boundary — omission/malformation is
  rejected before any API/Outbox/provider work; 409 on cross-call reuse
  mismatch); rate/quota enforced by the Mail service knobs (50/day, 5/min).
  v1 sends to exactly ONE recipient; no CC/BCC/attachments (HLD §3 manifest).

Reuses mcp-server/base.py (ShizuhaMCPBase: Shizuha ID OAuth bearer resolution,
API request plumbing, OAuth discovery/enforcement, run_mcp_server).
"""
import argparse
import json
import os
import sys
import uuid
from typing import Optional

from base import ShizuhaMCPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from base import ShizuhaMCPBase, run_mcp_server, _register_oauth_discovery_routes  # noqa: E402

# HLD §3: v1 search is a single bounded window, no continuation.
SEARCH_LIMIT_MAX = 50
LIST_LIMIT_MAX = 50


def parse_args():
    parser = argparse.ArgumentParser(description="Shizuha Mail MCP server (agent surface)")
    parser.add_argument("--jwt-token", help="JWT token (overrides SHIZUHA_MAIL_JWT_TOKEN)")
    parser.add_argument("--api-url", help="Mail API URL (overrides SHIZUHA_MAIL_URL)")
    return parser.parse_known_args()[0]


_args = parse_args()

MAIL_API_URL = (
    _args.api_url
    or os.environ.get("SHIZUHA_MAIL_URL")
    or os.environ.get("MAIL_API_URL")
)
MAIL_JWT_TOKEN = (
    _args.jwt_token
    or os.environ.get("SHIZUHA_MAIL_JWT_TOKEN")
    or os.environ.get("MAIL_JWT_TOKEN")
    or ""
)

mcp = ShizuhaMCPServer("shizuha-mail-agent", stateless_http=True)

_register_oauth_discovery_routes(mcp, "mail-agent")
base = ShizuhaMCPBase("mail-agent", 8014, api_url=MAIL_API_URL, jwt_token=MAIL_JWT_TOKEN)


# ---------------------------------------------------------------------------
# internal helpers — all untrusted-content-safe
# ---------------------------------------------------------------------------


def _bounded_limit(limit: Optional[int], maximum: int = LIST_LIMIT_MAX) -> int:
    """Clamp a caller-supplied limit into [1, maximum] (HLD §3 bounded window)."""
    try:
        value = int(limit)
    except (TypeError, ValueError):
        return 20
    return max(1, min(value, maximum))


def _account_info_block(account: dict) -> str:
    """Render account info. Never includes credentials or provider secrets."""
    display_name = account.get("display_name") or account.get("from_name") or ""
    email = account.get("email_address") or account.get("email") or ""
    quota = ""
    if account.get("storage_quota_mb") is not None:
        used = account.get("storage_used_mb") or 0
        quota = f" | quota {used}/{account['storage_quota_mb']} MB"
    return (
        f"- **{display_name or email or 'Unnamed'}** — {email}{quota}\n"
        f"  ID: {account.get('id')} | type: {account.get('incoming_server_type') or 'imap'}"
    )


def _message_summary(msg: dict) -> str:
    """A compact unbounded-safe one-line summary for list/search results."""
    subject = (msg.get("subject") or "(No Subject)").strip() or "(No Subject)"
    read_icon = "○" if not msg.get("is_read") else "✓"
    flag_icon = " ★" if msg.get("is_flagged") else ""
    sender = msg.get("from_email") or msg.get("from") or "Unknown"
    date = msg.get("date") or msg.get("received_at") or "N/A"
    return f"- [{read_icon}] **{subject[:120]}**{flag_icon}\n  From: {sender[:120]} | Date: {date} | ID: {msg.get('id')}"


# ---------------------------------------------------------------------------
# mail_account_info
# ---------------------------------------------------------------------------


@mcp.tool(name="mail_account_info")
def account_info() -> str:
    """
    Resolve the calling agent's canonical `@shizuha.com` mailbox and show its
    current quota/status. Self-scoped: this always reflects the authenticated
    agent's own account — there is no `account_id` parameter.
    """
    result = base.api_request("GET", "/accounts/me/")
    if "error" in result:
        return base.format_error(result)
    account = result.get("account") or result
    if not account or account.get("id") is None:
        return "No @shizuha.com mailbox provisioned for this agent yet."
    return f"# Mail account\n{_account_info_block(account)}"


# ---------------------------------------------------------------------------
# mail_list_messages
# ---------------------------------------------------------------------------


@mcp.tool(name="mail_list_messages")
def list_messages(folder: str = "inbox", unread_only: bool = False, limit: int = 20) -> str:
    """
    List messages in the calling agent's own mailbox (self-scoped).

    Args:
        folder: Folder role/name — "inbox" (default), "sent", "drafts",
            "trash", "junk", "archive", or a custom folder name.
        unread_only: Only show unread messages.
        limit: Max rows (bounded to 50).
    """
    params = {
        "folder": folder,
        "page_size": _bounded_limit(limit),
    }
    if unread_only:
        params["is_read"] = False
    result = base.api_request("GET", "/messages/", params=params)
    if "error" in result:
        return base.format_error(result)

    messages = result.get("results", result if isinstance(result, list) else [])
    if not messages:
        return f"No messages found in '{folder}'."

    lines = [f"# Messages — {folder} ({len(messages)})", ""]
    for msg in messages:
        lines.append(_message_summary(msg))
        lines.append("")
    return "\n".join(lines).rstrip()


# ---------------------------------------------------------------------------
# mail_get_message
# ---------------------------------------------------------------------------


@mcp.tool(name="mail_get_message")
def get_message(message_id: str) -> str:
    """
    Get the full content of a message by id.

    Self-scoped ownership: this agent can only read messages in its own
    mailbox. A foreign or nonexistent id returns the same generic not-found —
    no cross-account detail is leaked. Body is rendered as plain text only.

    Args:
        message_id: The message's id inside the caller's own mailbox.
    """
    result = base.api_request("GET", f"/messages/{message_id}/")
    if "error" in result:
        return base.format_error(result)

    msg = result
    subject = (msg.get("subject") or "(No Subject)").strip() or "(No Subject)"
    lines = [
        f"# {subject[:200]}",
        "",
        f"From: {msg.get('from_email') or msg.get('from') or 'Unknown'}",
        f"To: {', '.join(msg.get('to') or msg.get('to_addresses') or [])}",
        f"Date: {msg.get('date') or msg.get('received_at') or 'N/A'}",
        f"ID: {msg.get('id')}",
    ]
    if msg.get("is_flagged"):
        lines.append("Flagged: Yes")
    if msg.get("thread_id"):
        lines.append(f"Thread: {msg['thread_id']}")
    # Attachments metadata-only (HLD §3: never auto-fetch or decode).
    if msg.get("attachments"):
        lines.append("Attachments:")
        for att in msg["attachments"]:
            lines.append(f"- {att.get('filename') or 'unnamed'} ({att.get('size', 'N/A')} B)")
    lines.extend(["", "---", ""])
    # Plain-text only. body_html is deliberately omitted — untrusted content
    # must never render as active HTML from an agent-facing reader.
    body = msg.get("body_text")
    if body:
        lines.append(body)
    else:
        lines.append("(No plain-text body available for this message)")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# mail_search_messages
# ---------------------------------------------------------------------------


@mcp.tool(name="mail_search_messages")
def search_messages(
    query: str,
    folder: Optional[str] = None,
    has_attachments: Optional[bool] = None,
    is_read: Optional[bool] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    limit: int = 20,
) -> str:
    """
    Search the agent's own mailbox.

    HLD §3: v1 search is a SINGLE bounded window (limit <= 50) — no cursor,
    no offset continuation. Match on subject/body/from/to.

    Args:
        query: Search text.
        folder: Optional folder role/name to narrow to.
        has_attachments: Filter by attachment presence.
        is_read: Filter by read state.
        date_from: ISO date lower bound (e.g. 2026-01-01).
        date_to: ISO date upper bound.
        limit: Max results (clamped to 50).
    """
    limit = _bounded_limit(limit, maximum=SEARCH_LIMIT_MAX)
    if not query or not query.strip():
        return "Please provide a non-empty search query."
    data = {
        "q": query.strip(),
        "page_size": max(1, min(limit, SEARCH_LIMIT_MAX)),
    }
    if folder:
        data["folder"] = folder
    if has_attachments is not None:
        data["has_attachments"] = has_attachments
    if is_read is not None:
        data["is_read"] = is_read
    if date_from:
        data["date_from"] = date_from
    if date_to:
        data["date_to"] = date_to

    result = base.api_request("POST", "/messages/search/", data=data)
    if "error" in result:
        return base.format_error(result)

    messages = result.get("results", result if isinstance(result, list) else [])
    if not messages:
        return f"No messages found matching '{query.strip()}'."

    lines = [f"# Search: {query.strip()} ({len(messages)})", ""]
    for msg in messages:
        lines.append(_message_summary(msg))
        lines.append("")
    return "\n".join(lines).rstrip()


# ---------------------------------------------------------------------------
# mail_send_email
# ---------------------------------------------------------------------------


@mcp.tool(name="mail_send_email")
def send_email(to: str, subject: str, body: str, client_message_id: str) -> str:
    """
    Send an email from the agent's own provisioned @shizuha.com mailbox.

    Explicit invocation only (HLD §8: mail:send is sensitive, default-deny).
    Exactly-once semantics: you MUST pass a `client_message_id` (UUID). To retry
    a possibly-lost response, reuse the SAME key; the server dedupes and returns
    the original send status. A reused id with a DIFFERENT recipient/subject is
    a 409 conflict — never a silent overwrite. Rate limits (50/day, 5/min)
    enforced server-side.

    v1 sends to exactly ONE recipient and does not support CC/BCC/attachments
    (HLD §3 manifest). Multi-recipient fan-out is rejected at this boundary so a
    single admitted request can never amplify past one quota unit.

    Args:
        to: The single recipient email address.
        subject: Email subject.
        body: Plain-text body.
        client_message_id: REQUIRED UUID idempotency key. Generate a fresh UUID
            per send; reuse the SAME value to retry a lost response.
    """
    # MAIL-157: the idempotency key is unconditional at this boundary — reject
    # omission/malformation BEFORE any API/Outbox/provider work so a lost
    # response can always be replayed against the original admission.
    supplied_cmi = (client_message_id or "").strip()
    try:
        uuid.UUID(supplied_cmi)
    except (ValueError, TypeError):
        return "client_message_id is REQUIRED and must be a valid UUID."

    # MAIL-157: v1 one-recipient contract — reject 0 and >1 before any API work.
    to_list = [e.strip() for e in to.split(",") if e.strip()]
    if not to_list:
        return "Please provide exactly one recipient email."
    if len(to_list) > 1:
        return "v1 sends to exactly one recipient; multi-recipient sends are not supported."

    data = {
        "to_addresses": to_list,
        "subject": subject,
        "body_text": body,
        "client_message_id": supplied_cmi,
    }

    result = base.api_request("POST", "/send/", data=data)
    if "error" in result:
        return base.format_error(result)

    out = f"Email sent.\nTo: {', '.join(to_list)}\nSubject: {subject}\nStatus: {result.get('status', 'sent')}\n"
    cmi = result.get("client_message_id") or supplied_cmi or ""
    if cmi:
        out += f"client_message_id: {cmi}\n"
    if result.get("message") and "idempotent" in str(result.get("message", "")).lower():
        out += "(idempotent replay — this key was already sent)\n"
    return out.strip()


# ---------------------------------------------------------------------------
# mail_get_send_status
# ---------------------------------------------------------------------------


@mcp.tool(name="mail_get_send_status")
def get_send_status(client_message_id: str) -> str:
    """
    Get the durable send / provider-delivery status for a previously submitted
    email, keyed by the `client_message_id` returned from `mail_send_email`.

    Owner-scoped: only this agent's own send records are visible; a foreign or
    unknown key returns the same generic not-found.

    Args:
        client_message_id: The UUID idempotency key of the send.
    """
    cmi = (client_message_id or "").strip()
    try:
        uuid.UUID(cmi)
    except (ValueError, TypeError):
        return "client_message_id must be a valid UUID."
    result = base.api_request("GET", f"/send/{cmi}/")
    if "error" in result:
        return base.format_error(result)

    status = result.get("send_status") or result.get("status") or "unknown"
    lines = [f"# Send status — {cmi}", "", f"Send: {status}"]
    delivery = result.get("delivery_status")
    if delivery:
        lines.append(f"Delivery: {delivery}")
    if result.get("sent_at"):
        lines.append(f"Sent at: {result['sent_at']}")
    if result.get("delivery_diagnostic"):
        lines.append(f"Delivery detail: {result['delivery_diagnostic']}")
    return "\n".join(lines)


if __name__ == "__main__":
    run_mcp_server(mcp, base)
