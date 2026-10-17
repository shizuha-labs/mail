#!/usr/bin/env python3
"""
Shizuha Mail MCP Server.

Email client tools for AI agents.

Tools:
  Accounts:
  - mail_list_accounts: List mail accounts
  - mail_get_account: Get account details
  - mail_add_account: Add a new mail account
  - mail_update_account: Update account settings
  - mail_delete_account: Delete a mail account
  - mail_get_my_account: Get default account
  - mail_test_account: Test account connection
  - mail_test_connection: Test connection before adding
  - mail_set_default_account: Set default mail account
  - mail_update_preferences: Update mail preferences
  - mail_sync_accounts: Trigger account sync

  Providers:
  - mail_list_providers: List supported mail providers
  - mail_detect_provider: Auto-detect provider from email

  Folders:
  - mail_list_folders: List mail folders
  - mail_get_folder_tree: Get folder hierarchy tree
  - mail_create_folder: Create a new folder
  - mail_update_folder: Rename or move a folder
  - mail_delete_folder: Delete a folder

  Messages:
  - mail_list_messages: List messages in folder
  - mail_get_message: Get full message content
  - mail_move_messages: Move messages to folder
  - mail_flag_messages: Flag/unflag messages
  - mail_trash_messages: Move messages to trash
  - mail_delete_messages: Permanently delete messages
  - mail_search_messages: Search emails

  Threads:
  - mail_list_threads: List conversation threads
  - mail_get_thread: Get thread with all messages

  Drafts:
  - mail_list_drafts: List draft emails
  - mail_create_draft: Create a draft
  - mail_get_draft: Get draft details
  - mail_update_draft: Update a draft
  - mail_delete_draft: Delete a draft
  - mail_send_draft: Send a draft

  Send:
  - mail_send_email: Send an email
  - mail_reply_to_email: Reply to a message
  - mail_forward_email: Forward a message

  Filters:
  - mail_list_filters: List mail filters/rules
  - mail_create_filter: Create a mail filter
  - mail_get_filter: Get filter details
  - mail_update_filter: Update a filter
  - mail_delete_filter: Delete a filter

  Contacts:
  - mail_list_contacts: List contacts
  - mail_create_contact: Create a contact
  - mail_get_contact: Get contact details
  - mail_update_contact: Update a contact
  - mail_delete_contact: Delete a contact
  - mail_autocomplete_contacts: Autocomplete contact search
"""

import argparse
import base64
import binascii
import json
import os
import sys
from typing import Optional

from base import ShizuhaMCPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from base import ShizuhaMCPBase, run_mcp_server


def parse_args():
    """Parse command-line arguments for CLI argument compatibility."""
    parser = argparse.ArgumentParser(description="Shizuha Mail MCP Server")
    parser.add_argument(
        "--jwt-token",
        help="JWT token for authentication (overrides SHIZUHA_MAIL_JWT_TOKEN env var)",
    )
    parser.add_argument(
        "--api-url",
        help="Mail API URL (overrides SHIZUHA_MAIL_URL env var)",
    )
    args, _ = parser.parse_known_args()
    return args


# Parse CLI args (for callers that don't pass env vars to MCP subprocesses)
_args = parse_args()

# Configuration priority: CLI args > environment > defaults
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

mcp = ShizuhaMCPServer("shizuha-mail", stateless_http=True)

# Advertise OAuth 2.0 discovery metadata so MCP clients can discover the auth server.
from base import _register_oauth_discovery_routes as _reg_oauth_routes
_reg_oauth_routes(mcp, "mail")
base = ShizuhaMCPBase("mail", 8014, api_url=MAIL_API_URL, jwt_token=MAIL_JWT_TOKEN)

# Match the service's canonical 25 MiB upload ceiling by default.  The MCP
# forward boundary also applies it to each decoded attachment and to the
# aggregate carryover so base64 expansion cannot bypass the request policy.
MAX_FORWARD_ATTACHMENT_BYTES = int(os.environ.get("MAX_ATTACHMENT_SIZE", 26214400))
MAX_FORWARD_ATTACHMENTS_TOTAL_BYTES = int(
    os.environ.get("MAX_TOTAL_ATTACHMENT_SIZE", MAX_FORWARD_ATTACHMENT_BYTES)
)


# ---------------------------------------------------------------------------
# Accounts
# ---------------------------------------------------------------------------


@mcp.tool(name="mail_list_accounts")
def list_accounts() -> str:
    """
    List mail accounts.
    """
    result = base.api_request("GET", "/accounts/")
    if "error" in result:
        return base.format_error(result)

    accounts = (result if isinstance(result, list) else result.get("results", []))
    if not accounts:
        return "No mail accounts found."

    output = f"# Mail Accounts ({len(accounts)})\n\n"
    for acc in accounts:
        default_marker = " (default)" if acc.get("is_default") else ""
        output += f"- **{acc.get('email', acc.get('email_address', 'N/A'))}**{default_marker}\n"
        output += f"  ID: {acc.get('id')}\n"
        output += f"  Provider: {acc.get('provider', 'N/A')}\n"
        output += f"  Status: {acc.get('status', 'active')}\n"

    return output


@mcp.tool(name="mail_get_account")
def get_account(account_id: str) -> str:
    """
    Get detailed information about a specific mail account.

    Args:
        account_id: Account ID
    """
    result = base.api_request("GET", f"/accounts/{account_id}/")
    if "error" in result:
        return base.format_error(result)

    acc = result
    output = f"# Mail Account: {acc.get('email', acc.get('email_address', 'N/A'))}\n\n"
    output += f"**ID**: {acc.get('id')}\n"
    output += f"**Display Name**: {acc.get('display_name', 'N/A')}\n"
    output += f"**From Name**: {acc.get('from_name', 'N/A')}\n"
    output += f"**Provider**: {acc.get('provider', 'N/A')}\n"
    output += f"**Status**: {acc.get('status', 'active')}\n"
    output += f"**Default**: {acc.get('is_default', False)}\n"

    output += f"\n## Incoming Server\n"
    output += f"**Type**: {acc.get('incoming_server_type', 'N/A')}\n"
    output += f"**Host**: {acc.get('incoming_host', 'N/A')}\n"
    output += f"**Port**: {acc.get('incoming_port', 'N/A')}\n"
    output += f"**Security**: {acc.get('incoming_security', 'N/A')}\n"

    output += f"\n## Outgoing Server\n"
    output += f"**Host**: {acc.get('outgoing_host', 'N/A')}\n"
    output += f"**Port**: {acc.get('outgoing_port', 'N/A')}\n"
    output += f"**Security**: {acc.get('outgoing_security', 'N/A')}\n"

    if acc.get('last_sync'):
        output += f"\n**Last Sync**: {acc['last_sync']}\n"

    return output


@mcp.tool(name="mail_add_account")
def add_account(
    email_address: str,
    incoming_host: str,
    incoming_username: str,
    incoming_password: str,
    outgoing_host: str,
    display_name: str = "",
    from_name: str = "",
    incoming_server_type: str = "jmap",
    incoming_port: int = 443,
    incoming_security: str = "ssl",
    outgoing_port: int = 587,
    outgoing_security: str = "starttls",
    outgoing_username: str = "",
    outgoing_password: str = "",
    outgoing_requires_auth: bool = True,
) -> str:
    """
    Add a new mail account.

    Args:
        email_address: Email address for the account
        incoming_host: Incoming mail server hostname
        incoming_username: Username for incoming server
        incoming_password: Password for incoming server
        outgoing_host: Outgoing (SMTP) server hostname
        display_name: Display name for the account
        from_name: Name shown in From field
        incoming_server_type: Server type - 'jmap' or 'imap' (default: jmap)
        incoming_port: Incoming server port (default: 443 for JMAP, 993 for IMAP)
        incoming_security: Connection security - 'ssl', 'starttls', or 'none'
        outgoing_port: SMTP port (default: 587)
        outgoing_security: SMTP security - 'ssl', 'starttls', or 'none'
        outgoing_username: SMTP username (defaults to incoming_username if empty)
        outgoing_password: SMTP password (defaults to incoming_password if empty)
        outgoing_requires_auth: Whether SMTP requires authentication
    """
    data = {
        "email_address": email_address,
        "incoming_host": incoming_host,
        "incoming_username": incoming_username,
        "incoming_password": incoming_password,
        "outgoing_host": outgoing_host,
        "display_name": display_name or email_address,
        "from_name": from_name,
        "incoming_server_type": incoming_server_type,
        "incoming_port": incoming_port,
        "incoming_security": incoming_security,
        "outgoing_port": outgoing_port,
        "outgoing_security": outgoing_security,
        "outgoing_username": outgoing_username or incoming_username,
        "outgoing_password": outgoing_password or incoming_password,
        "outgoing_requires_auth": outgoing_requires_auth,
    }

    result = base.api_request("POST", "/accounts/", data=data)
    if "error" in result:
        return base.format_error(result)

    acc = result
    return (
        f"Mail account added!\n\n"
        f"**Email**: {acc.get('email_address', email_address)}\n"
        f"**ID**: {acc.get('id', 'N/A')}\n"
        f"**Incoming**: {incoming_server_type}://{incoming_host}:{incoming_port}\n"
        f"**Outgoing**: smtp://{outgoing_host}:{outgoing_port}\n"
    )


@mcp.tool(name="mail_update_account")
def update_account(
    account_id: str,
    display_name: Optional[str] = None,
    from_name: Optional[str] = None,
    incoming_host: Optional[str] = None,
    incoming_port: Optional[int] = None,
    incoming_security: Optional[str] = None,
    incoming_password: Optional[str] = None,
    outgoing_host: Optional[str] = None,
    outgoing_port: Optional[int] = None,
    outgoing_security: Optional[str] = None,
    outgoing_password: Optional[str] = None,
) -> str:
    """
    Update a mail account's settings.

    Args:
        account_id: Account ID to update
        display_name: New display name
        from_name: New From name
        incoming_host: New incoming server hostname
        incoming_port: New incoming server port
        incoming_security: New incoming security (ssl, starttls, none)
        incoming_password: New incoming server password
        outgoing_host: New outgoing server hostname
        outgoing_port: New outgoing server port
        outgoing_security: New outgoing security (ssl, starttls, none)
        outgoing_password: New outgoing server password
    """
    data = {}
    if display_name is not None:
        data["display_name"] = display_name
    if from_name is not None:
        data["from_name"] = from_name
    if incoming_host is not None:
        data["incoming_host"] = incoming_host
    if incoming_port is not None:
        data["incoming_port"] = incoming_port
    if incoming_security is not None:
        data["incoming_security"] = incoming_security
    if incoming_password is not None:
        data["incoming_password"] = incoming_password
    if outgoing_host is not None:
        data["outgoing_host"] = outgoing_host
    if outgoing_port is not None:
        data["outgoing_port"] = outgoing_port
    if outgoing_security is not None:
        data["outgoing_security"] = outgoing_security
    if outgoing_password is not None:
        data["outgoing_password"] = outgoing_password

    if not data:
        return "No fields to update. Provide at least one field to change."

    result = base.api_request("PATCH", f"/accounts/{account_id}/", data=data)
    if "error" in result:
        return base.format_error(result)

    acc = result
    return (
        f"Account updated!\n\n"
        f"**Email**: {acc.get('email', acc.get('email_address', 'N/A'))}\n"
        f"**ID**: {acc.get('id')}\n"
    )


@mcp.tool(name="mail_delete_account")
def delete_account(account_id: str) -> str:
    """
    Delete a mail account.

    Args:
        account_id: Account ID to delete
    """
    result = base.api_request("DELETE", f"/accounts/{account_id}/")
    if "error" in result:
        return base.format_error(result)

    return "Mail account deleted."


@mcp.tool(name="mail_get_my_account")
def get_my_account() -> str:
    """
    Get the default (primary) mail account for the authenticated user.
    """
    result = base.api_request("GET", "/accounts/me/")
    if "error" in result:
        return base.format_error(result)

    acc = result
    output = f"# My Default Account\n\n"
    output += f"**Email**: {acc.get('email', acc.get('email_address', 'N/A'))}\n"
    output += f"**ID**: {acc.get('id')}\n"
    output += f"**Display Name**: {acc.get('display_name', 'N/A')}\n"
    output += f"**Provider**: {acc.get('provider', 'N/A')}\n"
    output += f"**Status**: {acc.get('status', 'active')}\n"

    return output


@mcp.tool(name="mail_test_account")
def test_account(account_id: str) -> str:
    """
    Test an existing account's connection (incoming and outgoing).

    Args:
        account_id: Account ID to test
    """
    result = base.api_request("POST", f"/accounts/{account_id}/test/")
    if "error" in result:
        return base.format_error(result)

    output = f"# Connection Test Results\n\n"
    output += f"**Incoming**: {result.get('incoming', 'N/A')}\n"
    output += f"**Outgoing**: {result.get('outgoing', 'N/A')}\n"
    if result.get("incoming_error"):
        output += f"**Incoming Error**: {result['incoming_error']}\n"
    if result.get("outgoing_error"):
        output += f"**Outgoing Error**: {result['outgoing_error']}\n"
    if result.get("success"):
        output += "\nAll connections successful."

    return output


@mcp.tool(name="mail_test_connection")
def test_connection(
    incoming_host: str,
    incoming_username: str,
    incoming_password: str,
    outgoing_host: str,
    incoming_server_type: str = "jmap",
    incoming_port: int = 443,
    incoming_security: str = "ssl",
    outgoing_port: int = 587,
    outgoing_security: str = "starttls",
    outgoing_username: str = "",
    outgoing_password: str = "",
) -> str:
    """
    Test mail server connection before adding an account.

    Args:
        incoming_host: Incoming mail server hostname
        incoming_username: Username for incoming server
        incoming_password: Password for incoming server
        outgoing_host: Outgoing (SMTP) server hostname
        incoming_server_type: Server type - 'jmap' or 'imap'
        incoming_port: Incoming server port
        incoming_security: Connection security - 'ssl', 'starttls', or 'none'
        outgoing_port: SMTP port
        outgoing_security: SMTP security - 'ssl', 'starttls', or 'none'
        outgoing_username: SMTP username (defaults to incoming_username)
        outgoing_password: SMTP password (defaults to incoming_password)
    """
    data = {
        "incoming_host": incoming_host,
        "incoming_username": incoming_username,
        "incoming_password": incoming_password,
        "outgoing_host": outgoing_host,
        "incoming_server_type": incoming_server_type,
        "incoming_port": incoming_port,
        "incoming_security": incoming_security,
        "outgoing_port": outgoing_port,
        "outgoing_security": outgoing_security,
        "outgoing_username": outgoing_username or incoming_username,
        "outgoing_password": outgoing_password or incoming_password,
    }

    result = base.api_request("POST", "/accounts/test-connection/", data=data)
    if "error" in result:
        return base.format_error(result)

    output = f"# Connection Test Results\n\n"
    output += f"**Incoming ({incoming_server_type})**: {result.get('incoming', 'N/A')}\n"
    output += f"**Outgoing (SMTP)**: {result.get('outgoing', 'N/A')}\n"
    if result.get("incoming_error"):
        output += f"**Incoming Error**: {result['incoming_error']}\n"
    if result.get("outgoing_error"):
        output += f"**Outgoing Error**: {result['outgoing_error']}\n"
    if result.get("success"):
        output += "\nAll connections successful. You can now add this account."

    return output


@mcp.tool(name="mail_set_default_account")
def set_default_account(account_id: str) -> str:
    """
    Set a mail account as the default account for sending.

    Args:
        account_id: Account ID to set as default
    """
    result = base.api_request("POST", f"/accounts/{account_id}/set-default/")
    if "error" in result:
        return base.format_error(result)

    return f"Account {account_id} set as default."


@mcp.tool(name="mail_update_preferences")
def update_preferences(
    signature: Optional[str] = None,
    reply_behavior: Optional[str] = None,
    auto_mark_read: Optional[bool] = None,
    default_folder_view: Optional[str] = None,
) -> str:
    """
    Update mail preferences (signature, reply behavior, etc.).

    Args:
        signature: Email signature text
        reply_behavior: Default reply behavior ('reply' or 'reply_all')
        auto_mark_read: Automatically mark messages as read when opened
        default_folder_view: Default folder view on login
    """
    data = {}
    if signature is not None:
        data["signature"] = signature
    if reply_behavior is not None:
        data["reply_behavior"] = reply_behavior
    if auto_mark_read is not None:
        data["auto_mark_read"] = auto_mark_read
    if default_folder_view is not None:
        data["default_folder_view"] = default_folder_view

    if not data:
        return "No preferences to update. Provide at least one setting."

    result = base.api_request("PATCH", "/accounts/preferences/", data=data)
    if "error" in result:
        return base.format_error(result)

    return f"Mail preferences updated."


@mcp.tool(name="mail_sync_accounts")
def sync_accounts(account_id: Optional[str] = None) -> str:
    """
    Trigger a sync of mail accounts to fetch new messages.

    Args:
        account_id: Specific account ID to sync (syncs all if not provided)
    """
    data = {}
    if account_id:
        data["account_id"] = account_id

    result = base.api_request("POST", "/accounts/sync/", data=data)
    if "error" in result:
        return base.format_error(result)

    output = f"Sync triggered.\n\n"
    if result.get("synced_accounts"):
        output += f"**Accounts synced**: {result['synced_accounts']}\n"
    if result.get("new_messages"):
        output += f"**New messages**: {result['new_messages']}\n"

    return output


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------


@mcp.tool(name="mail_list_providers")
def list_providers() -> str:
    """
    List supported mail providers (Gmail, Outlook, etc.) with their default settings.
    """
    result = base.api_request("GET", "/providers/")
    if "error" in result:
        return base.format_error(result)

    providers = (result if isinstance(result, list) else result.get("results", []))
    if not providers:
        return "No providers found."

    output = f"# Mail Providers ({len(providers)})\n\n"
    for p in providers:
        output += f"## {p.get('name')}\n"
        output += f"**ID**: {p.get('id', p.get('slug', 'N/A'))}\n"
        if p.get("domains"):
            output += f"**Domains**: {', '.join(p['domains'])}\n"
        if p.get("incoming_host"):
            output += f"**Incoming**: {p.get('incoming_server_type', 'imap')}://{p['incoming_host']}:{p.get('incoming_port', 'N/A')}\n"
        if p.get("outgoing_host"):
            output += f"**Outgoing**: smtp://{p['outgoing_host']}:{p.get('outgoing_port', 'N/A')}\n"
        output += "\n"

    return output


@mcp.tool(name="mail_detect_provider")
def detect_provider(email_address: str) -> str:
    """
    Auto-detect the mail provider for an email address.

    Returns suggested server settings (host, port, security) based on the email domain.

    Args:
        email_address: Email address to detect provider for
    """
    result = base.api_request("GET", "/providers/detect/", params={"email": email_address})
    if "error" in result:
        return base.format_error(result)

    p = result
    output = f"# Detected Provider for {email_address}\n\n"
    output += f"**Provider**: {p.get('name', p.get('provider', 'Unknown'))}\n"

    if p.get("incoming_host"):
        output += f"\n## Incoming Server\n"
        output += f"**Type**: {p.get('incoming_server_type', 'imap')}\n"
        output += f"**Host**: {p['incoming_host']}\n"
        output += f"**Port**: {p.get('incoming_port', 'N/A')}\n"
        output += f"**Security**: {p.get('incoming_security', 'ssl')}\n"

    if p.get("outgoing_host"):
        output += f"\n## Outgoing Server\n"
        output += f"**Host**: {p['outgoing_host']}\n"
        output += f"**Port**: {p.get('outgoing_port', 'N/A')}\n"
        output += f"**Security**: {p.get('outgoing_security', 'starttls')}\n"

    return output


# ---------------------------------------------------------------------------
# Folders
# ---------------------------------------------------------------------------


@mcp.tool(name="mail_list_folders")
def list_folders(account_id: Optional[str] = None) -> str:
    """
    List mail folders.

    Args:
        account_id: Mail account ID (uses default if not specified)
    """
    params = {}
    if account_id:
        params["account"] = account_id

    result = base.api_request("GET", "/folders/", params=params)
    if "error" in result:
        return base.format_error(result)

    folders = (result if isinstance(result, list) else result.get("results", []))
    if not folders:
        return "No folders found."

    output = f"# Mail Folders ({len(folders)})\n\n"
    for f in folders:
        icon = {
            "inbox": "📥",
            "sent": "📤",
            "drafts": "📝",
            "trash": "🗑️",
            "spam": "⚠️",
            "archive": "📦",
        }.get(f.get("folder_type", "").lower(), "📁")
        unread = f.get("unread_count", 0)
        unread_str = f" ({unread} unread)" if unread > 0 else ""
        output += f"- {icon} **{f.get('name')}**{unread_str}\n"
        output += f"  ID: {f.get('id')}\n"
        output += f"  Messages: {f.get('message_count', 0)}\n"

    return output


@mcp.tool(name="mail_get_folder_tree")
def get_folder_tree(account_id: Optional[str] = None) -> str:
    """
    Get folder hierarchy as a nested tree structure.

    Args:
        account_id: Mail account ID (uses default if not specified)
    """
    params = {}
    if account_id:
        params["account"] = account_id

    result = base.api_request("GET", "/folders/tree/", params=params)
    if "error" in result:
        return base.format_error(result)

    folders = (result if isinstance(result, list) else result.get("results", []))
    if not folders:
        return "No folders found."

    output = "# Folder Tree\n\n"

    def render_tree(items, indent=0):
        nonlocal output
        for f in items:
            prefix = "  " * indent
            unread = f.get("unread_count", 0)
            unread_str = f" ({unread})" if unread > 0 else ""
            output += f"{prefix}- **{f.get('name')}**{unread_str} (ID: {f.get('id')})\n"
            children = f.get("children", [])
            if children:
                render_tree(children, indent + 1)

    render_tree(folders)
    return output


@mcp.tool(name="mail_create_folder")
def create_folder(
    name: str,
    account_id: Optional[str] = None,
    parent_id: Optional[str] = None,
) -> str:
    """
    Create a new mail folder.

    Args:
        name: Folder name
        account_id: Mail account ID
        parent_id: Parent folder ID for creating nested folders
    """
    data = {"name": name}
    if account_id:
        data["account"] = account_id
    if parent_id:
        data["parent"] = parent_id

    result = base.api_request("POST", "/folders/", data=data)
    if "error" in result:
        return base.format_error(result)

    f = result
    return (
        f"Folder created!\n\n"
        f"**Name**: {f.get('name')}\n"
        f"**ID**: {f.get('id')}\n"
    )


@mcp.tool(name="mail_update_folder")
def update_folder(
    folder_id: str,
    name: Optional[str] = None,
    parent_id: Optional[str] = None,
) -> str:
    """
    Update a folder (rename or move).

    Args:
        folder_id: Folder ID to update
        name: New folder name
        parent_id: New parent folder ID (to move the folder)
    """
    data = {}
    if name is not None:
        data["name"] = name
    if parent_id is not None:
        data["parent"] = parent_id

    if not data:
        return "No fields to update. Provide a new name or parent_id."

    result = base.api_request("PATCH", f"/folders/{folder_id}/", data=data)
    if "error" in result:
        return base.format_error(result)

    f = result
    return f"Folder updated: **{f.get('name')}** (ID: {f.get('id')})"


@mcp.tool(name="mail_delete_folder")
def delete_folder(folder_id: str) -> str:
    """
    Delete a mail folder.

    Args:
        folder_id: Folder ID to delete
    """
    result = base.api_request("DELETE", f"/folders/{folder_id}/")
    if "error" in result:
        return base.format_error(result)

    return "Folder deleted."


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------

# Canonical system folder roles the Mail REST API resolves server-side.
# Mirrors SPECIAL_FOLDER_ROLES in mail/folder_tree.py.
SYSTEM_FOLDER_ROLES = frozenset(
    {"inbox", "drafts", "sent", "trash", "junk", "archive"}
)


def folder_query_params(folder_name: Optional[str]) -> dict:
    """
    Build the REST folder selector for a folder referred to by name.

    ``GET /messages/`` treats ``folder`` as a server-side mailbox id (or a local
    MailFolder UUID) and only resolves the canonical system folders when they
    arrive as ``folder_role``. Sending ``folder=inbox`` therefore matches
    nothing, and ``folder=Inbox`` is forwarded to JMAP as a raw mailbox id and
    fails upstream. Map the canonical roles (case-insensitively) onto
    ``folder_role``; pass any other name through as ``folder`` unchanged.
    """
    name = (folder_name or "").strip()
    if not name:
        return {"folder_role": "inbox"}
    if name.lower() in SYSTEM_FOLDER_ROLES:
        return {"folder_role": name.lower()}
    return {"folder": name}


@mcp.tool(name="mail_list_messages")
def list_messages(
    folder_id: Optional[str] = None,
    folder_name: str = "inbox",
    unread_only: bool = False,
    flagged_only: bool = False,
    limit: int = 20,
) -> str:
    """
    List messages in a folder.

    Args:
        folder_id: Folder ID
        folder_name: Folder name if ID not provided. The standard system
            folders (inbox, drafts, sent, trash, junk, archive) are matched
            case-insensitively and resolved server-side; any other value is
            treated as a server-side mailbox id/name.
        unread_only: Show only unread messages
        flagged_only: Show only flagged/starred messages
        limit: Maximum messages to return
    """
    if folder_id:
        result = base.api_request(
            "GET", f"/folders/{folder_id}/messages/", params={"page_size": limit}
        )
    else:
        params = dict(folder_query_params(folder_name))
        params["page_size"] = limit
        if unread_only:
            params["is_read"] = False
        if flagged_only:
            params["is_flagged"] = True
        result = base.api_request("GET", "/messages/", params=params)

    if "error" in result:
        return base.format_error(result)

    messages = (result if isinstance(result, list) else result.get("results", []))
    if not messages:
        return "No messages found."

    output = f"# Messages ({len(messages)})\n\n"
    for msg in messages:
        read_icon = "○" if not msg.get("is_read") else "✓"
        flag_icon = " ★" if msg.get("is_flagged") else ""
        output += f"- [{read_icon}] **{msg.get('subject', '(No Subject)')}**{flag_icon}\n"
        output += f"  From: {msg.get('from_email', msg.get('from', 'Unknown'))}\n"
        output += f"  Date: {msg.get('date', msg.get('received_at', 'N/A'))}\n"
        output += f"  ID: {msg.get('id')}\n"
        if msg.get("has_attachments"):
            output += f"  📎 Has attachments\n"
        output += "\n"

    return output


@mcp.tool(name="mail_get_message")
def get_message(message_id: str) -> str:
    """
    Get full message content.

    Args:
        message_id: Message ID
    """
    result = base.api_request("GET", f"/messages/{message_id}/")
    if "error" in result:
        return base.format_error(result)

    msg = result
    output = f"# {msg.get('subject', '(No Subject)')}\n\n"
    output += f"**From**: {msg.get('from_email', msg.get('from', 'Unknown'))}\n"
    output += f"**To**: {msg.get('to', [])}\n"
    if msg.get("cc"):
        output += f"**CC**: {msg['cc']}\n"
    if msg.get("bcc"):
        output += f"**BCC**: {msg['bcc']}\n"
    output += f"**Date**: {msg.get('date', msg.get('received_at', 'N/A'))}\n"
    output += f"**ID**: {msg.get('id')}\n"
    if msg.get("is_flagged"):
        output += f"**Flagged**: Yes\n"
    if msg.get("thread_id"):
        output += f"**Thread ID**: {msg['thread_id']}\n"

    if msg.get("attachments"):
        output += f"\n## Attachments\n"
        for att in msg["attachments"]:
            output += f"- {att.get('filename')} ({att.get('size', 'N/A')})\n"

    output += f"\n---\n\n"

    # Body content
    if msg.get("body_text"):
        output += msg["body_text"]
    elif msg.get("body_html"):
        output += "(HTML content - plain text not available)"
    else:
        output += "(No content)"

    return output


@mcp.tool(name="mail_move_messages")
def move_messages(message_ids: str, folder_id: str) -> str:
    """
    Move one or more messages to a folder.

    Args:
        message_ids: Comma-separated message IDs to move
        folder_id: Destination folder ID
    """
    ids = [mid.strip() for mid in message_ids.split(",")]
    data = {
        "message_ids": ids,
        "folder_id": folder_id,
    }

    result = base.api_request("POST", "/messages/move/", data=data)
    if "error" in result:
        return base.format_error(result)

    count = len(ids)
    undo_id = result.get("undo_id")
    undo_hint = f" (undo_id={undo_id})" if undo_id else ""
    return f"{count} message(s) moved to folder.{undo_hint}"


@mcp.tool(name="mail_undo_messages")
def undo_messages(undo_id: str) -> str:
    """
    Undo a reversible bulk operation (move/flag/trash) within the bounded
    undo window. Pass the undo_id returned by the original bulk operation.
    Permanent delete cannot be undone.

    Args:
        undo_id: The undo_id returned by mail_move_messages / mail_flag_messages /
            mail_trash_messages
    """
    result = base.api_request("POST", "/messages/undo/", data={"undo_id": undo_id})
    if "error" in result:
        return base.format_error(result)

    undone = result.get("undone", [])
    failed = result.get("failed", {})
    summary = f"{len(undone)} message(s) restored ({result.get('op_type')})."
    if failed:
        summary += f" {len(failed)} failed: {failed}"
    return summary


@mcp.tool(name="mail_flag_messages")
def flag_messages(message_ids: str, is_flagged: bool = True) -> str:
    """
    Flag or unflag one or more messages (star/unstar).

    Args:
        message_ids: Comma-separated message IDs
        is_flagged: True to flag, False to unflag
    """
    ids = [mid.strip() for mid in message_ids.split(",")]
    data = {
        "message_ids": ids,
        "is_flagged": is_flagged,
    }

    result = base.api_request("POST", "/messages/flag/", data=data)
    if "error" in result:
        return base.format_error(result)

    action = "flagged" if is_flagged else "unflagged"
    undo_id = result.get("undo_id")
    undo_hint = f" (undo_id={undo_id})" if undo_id else ""
    return f"{len(ids)} message(s) {action}.{undo_hint}"


@mcp.tool(name="mail_trash_messages")
def trash_messages(message_ids: str) -> str:
    """
    Move one or more messages to trash.

    Args:
        message_ids: Comma-separated message IDs to trash
    """
    ids = [mid.strip() for mid in message_ids.split(",")]
    data = {"message_ids": ids}

    result = base.api_request("POST", "/messages/trash/", data=data)
    if "error" in result:
        return base.format_error(result)

    undo_id = result.get("undo_id")
    undo_hint = f" (undo_id={undo_id})" if undo_id else ""
    return f"{len(ids)} message(s) moved to trash.{undo_hint}"


@mcp.tool(name="mail_delete_messages")
def delete_messages(message_ids: str) -> str:
    """
    Permanently delete one or more messages. This cannot be undone.

    Args:
        message_ids: Comma-separated message IDs to delete permanently
    """
    ids = [mid.strip() for mid in message_ids.split(",")]
    data = {"message_ids": ids}

    result = base.api_request("POST", "/messages/delete/", data=data)
    if "error" in result:
        return base.format_error(result)

    return f"{len(ids)} message(s) permanently deleted."


@mcp.tool(name="mail_search_messages")
def search_messages(
    query: str,
    folder: Optional[str] = None,
    account_id: Optional[str] = None,
    has_attachments: Optional[bool] = None,
    is_read: Optional[bool] = None,
    is_flagged: Optional[bool] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    limit: int = 20,
) -> str:
    """
    Search emails across folders.

    Args:
        query: Search query (searches subject, body, from, to)
        folder: Limit to specific folder name or ID
        account_id: Limit to specific account
        has_attachments: Filter by attachment presence
        is_read: Filter by read status
        is_flagged: Filter by flagged status
        date_from: Start date (ISO format, e.g. 2026-01-01)
        date_to: End date (ISO format)
        limit: Maximum results
    """
    data = {"q": query, "page_size": limit}
    if folder:
        data["folder"] = folder
    if account_id:
        data["account"] = account_id
    if has_attachments is not None:
        data["has_attachments"] = has_attachments
    if is_read is not None:
        data["is_read"] = is_read
    if is_flagged is not None:
        data["is_flagged"] = is_flagged
    if date_from:
        data["date_from"] = date_from
    if date_to:
        data["date_to"] = date_to

    result = base.api_request("POST", "/messages/search/", data=data)
    if "error" in result:
        return base.format_error(result)

    messages = (result if isinstance(result, list) else result.get("results", []))
    if not messages:
        return f"No messages found matching '{query}'."

    output = f"# Search Results: '{query}' ({len(messages)})\n\n"
    for msg in messages:
        read_icon = "○" if not msg.get("is_read") else "✓"
        output += f"- [{read_icon}] **{msg.get('subject', '(No Subject)')}**\n"
        output += f"  From: {msg.get('from_email', 'Unknown')}\n"
        output += f"  Date: {msg.get('date', 'N/A')}\n"
        output += f"  Folder: {msg.get('folder_name', 'N/A')}\n"
        output += f"  ID: {msg.get('id')}\n\n"

    return output


# ---------------------------------------------------------------------------
# Threads
# ---------------------------------------------------------------------------


@mcp.tool(name="mail_list_threads")
def list_threads(
    folder_id: Optional[str] = None,
    folder_name: str = "inbox",
    limit: int = 20,
) -> str:
    """
    List conversation threads.

    Args:
        folder_id: Folder ID to list threads from
        folder_name: Folder name if ID not provided. The standard system
            folders (inbox, drafts, sent, trash, junk, archive) are matched
            case-insensitively and resolved server-side; any other value is
            treated as a server-side mailbox id/name.
        limit: Maximum threads to return
    """
    params = {"page_size": limit}
    if folder_id:
        params["folder"] = folder_id
    else:
        params.update(folder_query_params(folder_name))

    result = base.api_request("GET", "/threads/", params=params)
    if "error" in result:
        return base.format_error(result)

    threads = (result if isinstance(result, list) else result.get("results", []))
    if not threads:
        return "No threads found."

    output = f"# Threads ({len(threads)})\n\n"
    for t in threads:
        unread = t.get("unread_count", 0)
        unread_str = f" ({unread} unread)" if unread > 0 else ""
        output += f"- **{t.get('subject', '(No Subject)')}**{unread_str}\n"
        output += f"  Messages: {t.get('message_count', 0)}\n"
        output += f"  Participants: {', '.join(t.get('participants', []))}\n"
        output += f"  Last: {t.get('last_message_date', 'N/A')}\n"
        output += f"  ID: {t.get('id')}\n\n"

    return output


@mcp.tool(name="mail_get_thread")
def get_thread(thread_id: str) -> str:
    """
    Get a conversation thread with all its messages.

    Args:
        thread_id: Thread ID
    """
    result = base.api_request("GET", f"/threads/{thread_id}/")
    if "error" in result:
        return base.format_error(result)

    t = result
    output = f"# Thread: {t.get('subject', '(No Subject)')}\n\n"
    output += f"**ID**: {t.get('id')}\n"
    output += f"**Messages**: {t.get('message_count', 0)}\n"
    if t.get("participants"):
        output += f"**Participants**: {', '.join(t['participants'])}\n"
    output += "\n---\n\n"

    messages = t.get("messages", [])
    for i, msg in enumerate(messages, 1):
        output += f"## Message {i}\n"
        output += f"**From**: {msg.get('from_email', msg.get('from', 'Unknown'))}\n"
        output += f"**Date**: {msg.get('date', msg.get('received_at', 'N/A'))}\n"
        output += f"**ID**: {msg.get('id')}\n\n"
        if msg.get("body_text"):
            output += msg["body_text"]
        elif msg.get("body_html"):
            output += "(HTML content)"
        else:
            output += "(No content)"
        output += "\n\n---\n\n"

    return output


# ---------------------------------------------------------------------------
# Drafts
# ---------------------------------------------------------------------------


@mcp.tool(name="mail_list_drafts")
def list_drafts(account_id: Optional[str] = None, limit: int = 20) -> str:
    """
    List draft emails.

    Args:
        account_id: Filter by account ID
        limit: Maximum drafts to return
    """
    params = {"page_size": limit}
    if account_id:
        params["account"] = account_id

    result = base.api_request("GET", "/drafts/", params=params)
    if "error" in result:
        return base.format_error(result)

    drafts = (result if isinstance(result, list) else result.get("results", []))
    if not drafts:
        return "No drafts found."

    output = f"# Drafts ({len(drafts)})\n\n"
    for d in drafts:
        to_str = ", ".join(d.get("to", [])) if isinstance(d.get("to"), list) else d.get("to", "N/A")
        output += f"- **{d.get('subject', '(No Subject)')}**\n"
        output += f"  To: {to_str}\n"
        output += f"  Updated: {d.get('updated_at', d.get('date', 'N/A'))}\n"
        output += f"  ID: {d.get('id')}\n\n"

    return output


@mcp.tool(name="mail_create_draft")
def create_draft(
    subject: str,
    body: str,
    to: Optional[str] = None,
    cc: Optional[str] = None,
    bcc: Optional[str] = None,
    account_id: Optional[str] = None,
) -> str:
    """
    Create an email draft.

    Args:
        subject: Email subject
        body: Email body (plain text)
        to: Recipient email(s), comma-separated
        cc: CC recipients, comma-separated
        bcc: BCC recipients, comma-separated
        account_id: Account to create draft in
    """
    data = {
        "subject": subject,
        "body_text": body,
    }
    if to:
        data["to"] = [e.strip() for e in to.split(",")]
    if cc:
        data["cc"] = [e.strip() for e in cc.split(",")]
    if bcc:
        data["bcc"] = [e.strip() for e in bcc.split(",")]
    if account_id:
        data["account_id"] = account_id

    result = base.api_request("POST", "/drafts/", data=data)
    if "error" in result:
        return base.format_error(result)

    draft = result
    return (
        f"Draft saved!\n\n"
        f"**ID**: {draft.get('id')}\n"
        f"**Subject**: {draft.get('subject')}\n"
    )


@mcp.tool(name="mail_get_draft")
def get_draft(draft_id: str) -> str:
    """
    Get a draft's full content.

    Args:
        draft_id: Draft ID
    """
    result = base.api_request("GET", f"/drafts/{draft_id}/")
    if "error" in result:
        return base.format_error(result)

    d = result
    output = f"# Draft: {d.get('subject', '(No Subject)')}\n\n"
    output += f"**ID**: {d.get('id')}\n"
    to_str = ", ".join(d.get("to", [])) if isinstance(d.get("to"), list) else d.get("to", "N/A")
    output += f"**To**: {to_str}\n"
    if d.get("cc"):
        cc_str = ", ".join(d["cc"]) if isinstance(d["cc"], list) else d["cc"]
        output += f"**CC**: {cc_str}\n"
    if d.get("bcc"):
        bcc_str = ", ".join(d["bcc"]) if isinstance(d["bcc"], list) else d["bcc"]
        output += f"**BCC**: {bcc_str}\n"
    output += f"**Updated**: {d.get('updated_at', 'N/A')}\n"

    output += f"\n---\n\n"
    if d.get("body_text"):
        output += d["body_text"]
    elif d.get("body_html"):
        output += "(HTML content)"
    else:
        output += "(Empty draft)"

    return output


@mcp.tool(name="mail_update_draft")
def update_draft(
    draft_id: str,
    subject: Optional[str] = None,
    body: Optional[str] = None,
    to: Optional[str] = None,
    cc: Optional[str] = None,
    bcc: Optional[str] = None,
) -> str:
    """
    Update an existing draft.

    Args:
        draft_id: Draft ID to update
        subject: New subject
        body: New body text
        to: New recipient(s), comma-separated
        cc: New CC recipients, comma-separated
        bcc: New BCC recipients, comma-separated
    """
    data = {}
    if subject is not None:
        data["subject"] = subject
    if body is not None:
        data["body_text"] = body
    if to is not None:
        data["to"] = [e.strip() for e in to.split(",")]
    if cc is not None:
        data["cc"] = [e.strip() for e in cc.split(",")]
    if bcc is not None:
        data["bcc"] = [e.strip() for e in bcc.split(",")]

    if not data:
        return "No fields to update. Provide at least one field to change."

    result = base.api_request("PATCH", f"/drafts/{draft_id}/", data=data)
    if "error" in result:
        return base.format_error(result)

    d = result
    return f"Draft updated: **{d.get('subject', '(No Subject)')}** (ID: {d.get('id')})"


@mcp.tool(name="mail_delete_draft")
def delete_draft(draft_id: str) -> str:
    """
    Delete a draft.

    Args:
        draft_id: Draft ID to delete
    """
    result = base.api_request("DELETE", f"/drafts/{draft_id}/")
    if "error" in result:
        return base.format_error(result)

    return "Draft deleted."


@mcp.tool(name="mail_send_draft")
def send_draft(draft_id: str) -> str:
    """
    Send an existing draft.

    Args:
        draft_id: Draft ID to send
    """
    result = base.api_request("POST", f"/drafts/{draft_id}/send/")
    if "error" in result:
        return base.format_error(result)

    return f"Draft sent! Message ID: {result.get('id', result.get('message_id', 'N/A'))}"


# ---------------------------------------------------------------------------
# Send / Reply
# ---------------------------------------------------------------------------


@mcp.tool(name="mail_send_email")
def send_email(
    to: str,
    subject: str,
    body: str,
    cc: Optional[str] = None,
    bcc: Optional[str] = None,
    account_id: Optional[str] = None,
) -> str:
    """
    Send an email.

    Args:
        to: Recipient email(s), comma-separated
        subject: Email subject
        body: Email body (plain text)
        cc: CC recipients, comma-separated
        bcc: BCC recipients, comma-separated
        account_id: Send from specific account
    """
    to_list = [e.strip() for e in to.split(",")]

    data = {
        "to_addresses": to_list,
        "subject": subject,
        "body_text": body,
    }
    if cc:
        data["cc_addresses"] = [e.strip() for e in cc.split(",")]
    if bcc:
        data["bcc_addresses"] = [e.strip() for e in bcc.split(",")]
    if account_id:
        data["account_id"] = account_id

    result = base.api_request("POST", "/send/", data=data)
    if "error" in result:
        return base.format_error(result)

    msg = result
    return (
        f"Email sent!\n\n"
        f"**To**: {', '.join(to_list)}\n"
        f"**Subject**: {subject}\n"
        f"**Message ID**: {msg.get('id', 'N/A')}\n"
    )


@mcp.tool(name="mail_reply_to_email")
def reply_to_email(
    message_id: str,
    body: str,
    reply_all: bool = False,
) -> str:
    """
    Reply to an email.

    Args:
        message_id: Original message ID
        body: Reply body
        reply_all: Reply to all recipients
    """
    source = base.api_request("GET", f"/messages/{message_id}/")
    if "error" in source:
        return base.format_error(source)

    sender = source.get("reply_to") or source.get("from_address")
    recipients = [sender] if sender else []
    cc_recipients = []
    if reply_all:
        for address in source.get("to_addresses", []) + source.get("cc_addresses", []):
            if address and address not in recipients and address not in cc_recipients:
                cc_recipients.append(address)
    source_id = source.get("message_id")
    references = list(source.get("references") or [])
    if source.get("in_reply_to") and source["in_reply_to"] not in references:
        references.append(source["in_reply_to"])
    if source_id and source_id not in references:
        references.append(source_id)
    subject = source.get("subject") or ""
    if not subject.lower().startswith("re:"):
        subject = f"Re: {subject}"
    quote = _quoted_source(source)
    data = {
        "account_id": source.get("account_id"),
        "to_addresses": recipients,
        "cc_addresses": cc_recipients,
        "subject": subject,
        "body_text": f"{body.rstrip()}\n\n{quote}",
        "in_reply_to": source_id or source.get("in_reply_to", ""),
        "references": references,
        "reply_to_message_id": message_id,
    }
    result = base.api_request("POST", "/send/", data=data)
    if "error" in result:
        return base.format_error(result)

    return f"Reply sent!"


def _quoted_source(source: dict) -> str:
    """Return the stable plain-text quote used by reply and forward tools."""
    sent = source.get("date_sent") or source.get("date_received") or "unknown date"
    sender = source.get("from_address") or "unknown sender"
    text = source.get("body_text") or source.get("text_body") or source.get("preview") or ""
    quoted = "\n".join(f"> {line}" if line else ">" for line in text.rstrip().splitlines())
    return f"On {sent}, {sender} wrote:\n{quoted}"


def _permitted_forward_attachments(attachments: list[dict]) -> tuple[list[dict], str]:
    """Validate forwarded attachment content and decoded size, fail closed."""
    total = 0
    for attachment in attachments:
        content = attachment.get("content")
        if not isinstance(content, str) or not content:
            return [], "source attachments are not available for permitted carryover"
        encoded = content.split(",", 1)[1] if content.startswith("data:") and "," in content else content
        try:
            decoded_size = len(base64.b64decode(encoded, validate=True))
        except (binascii.Error, ValueError):
            return [], "source attachment content is not valid base64"
        if decoded_size > MAX_FORWARD_ATTACHMENT_BYTES:
            return [], f"source attachment exceeds {MAX_FORWARD_ATTACHMENT_BYTES} decoded bytes"
        total += decoded_size
        if total > MAX_FORWARD_ATTACHMENTS_TOTAL_BYTES:
            return [], (
                "source attachments exceed "
                f"{MAX_FORWARD_ATTACHMENTS_TOTAL_BYTES} aggregate decoded bytes"
            )
    return attachments, ""


@mcp.tool(name="mail_forward_email")
def forward_email(
    message_id: str,
    to: str,
    body: str = "",
    include_attachments: bool = False,
) -> str:
    """Forward an email; source attachments are included only when explicitly requested."""
    source = base.api_request("GET", f"/messages/{message_id}/")
    if "error" in source:
        return base.format_error(source)
    subject = source.get("subject") or ""
    if not subject.lower().startswith("fwd:"):
        subject = f"Fwd: {subject}"
    data = {
        "account_id": source.get("account_id"),
        "to_addresses": [address.strip() for address in to.split(",") if address.strip()],
        "subject": subject,
        "body_text": f"{body.rstrip()}\n\n---------- Forwarded message ----------\n{_quoted_source(source)}".lstrip(),
    }
    if include_attachments:
        attachments = source.get("attachments") or []
        attachments, error = _permitted_forward_attachments(attachments)
        if error:
            return f"Error: {error}"
        data["attachments"] = attachments
    result = base.api_request("POST", "/send/", data=data)
    if "error" in result:
        return base.format_error(result)
    return "Email forwarded!"


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------


@mcp.tool(name="mail_list_filters")
def list_filters(account_id: Optional[str] = None) -> str:
    """
    List mail filters/rules.

    Args:
        account_id: Filter by account ID
    """
    params = {}
    if account_id:
        params["account"] = account_id

    result = base.api_request("GET", "/filters/", params=params)
    if "error" in result:
        return base.format_error(result)

    filters = (result if isinstance(result, list) else result.get("results", []))
    if not filters:
        return "No mail filters found."

    output = f"# Mail Filters ({len(filters)})\n\n"
    for f in filters:
        status = "Enabled" if f.get("is_active", True) else "Disabled"
        output += f"- **{f.get('name', 'Unnamed filter')}** [{status}]\n"
        output += f"  ID: {f.get('id')}\n"
        if f.get("condition"):
            output += f"  Condition: {f['condition']}\n"
        if f.get("action"):
            output += f"  Action: {f['action']}\n"
        output += "\n"

    return output


@mcp.tool(name="mail_create_filter")
def create_filter(
    name: str,
    condition_field: str,
    condition_operator: str,
    condition_value: str,
    action_type: str,
    action_value: Optional[str] = None,
    account_id: Optional[str] = None,
    is_active: bool = True,
) -> str:
    """
    Create a mail filter/rule.

    Args:
        name: Filter name
        condition_field: Field to match (from, to, subject, body, has_attachments)
        condition_operator: Operator (contains, equals, starts_with, ends_with, regex)
        condition_value: Value to match against
        action_type: Action to perform (move, label, mark_read, trash, archive, flag)
        action_value: Action parameter (e.g., folder ID for 'move', label name for 'label')
        account_id: Apply to specific account
        is_active: Whether filter is active
    """
    data = {
        "name": name,
        "condition_field": condition_field,
        "condition_operator": condition_operator,
        "condition_value": condition_value,
        "action_type": action_type,
        "is_active": is_active,
    }
    if action_value:
        data["action_value"] = action_value
    if account_id:
        data["account"] = account_id

    result = base.api_request("POST", "/filters/", data=data)
    if "error" in result:
        return base.format_error(result)

    f = result
    return (
        f"Filter created!\n\n"
        f"**Name**: {f.get('name')}\n"
        f"**ID**: {f.get('id')}\n"
        f"**Condition**: {condition_field} {condition_operator} '{condition_value}'\n"
        f"**Action**: {action_type} {action_value or ''}\n"
    )


@mcp.tool(name="mail_get_filter")
def get_filter(filter_id: str) -> str:
    """
    Get details of a specific mail filter.

    Args:
        filter_id: Filter ID
    """
    result = base.api_request("GET", f"/filters/{filter_id}/")
    if "error" in result:
        return base.format_error(result)

    f = result
    status = "Enabled" if f.get("is_active", True) else "Disabled"
    output = f"# Filter: {f.get('name', 'Unnamed')}\n\n"
    output += f"**ID**: {f.get('id')}\n"
    output += f"**Status**: {status}\n"
    output += f"**Condition Field**: {f.get('condition_field', 'N/A')}\n"
    output += f"**Condition Operator**: {f.get('condition_operator', 'N/A')}\n"
    output += f"**Condition Value**: {f.get('condition_value', 'N/A')}\n"
    output += f"**Action Type**: {f.get('action_type', 'N/A')}\n"
    if f.get("action_value"):
        output += f"**Action Value**: {f['action_value']}\n"
    if f.get("created_at"):
        output += f"**Created**: {f['created_at']}\n"

    return output


@mcp.tool(name="mail_update_filter")
def update_filter(
    filter_id: str,
    name: Optional[str] = None,
    condition_field: Optional[str] = None,
    condition_operator: Optional[str] = None,
    condition_value: Optional[str] = None,
    action_type: Optional[str] = None,
    action_value: Optional[str] = None,
    is_active: Optional[bool] = None,
) -> str:
    """
    Update a mail filter.

    Args:
        filter_id: Filter ID to update
        name: New filter name
        condition_field: New condition field
        condition_operator: New condition operator
        condition_value: New condition value
        action_type: New action type
        action_value: New action value
        is_active: Enable or disable the filter
    """
    data = {}
    if name is not None:
        data["name"] = name
    if condition_field is not None:
        data["condition_field"] = condition_field
    if condition_operator is not None:
        data["condition_operator"] = condition_operator
    if condition_value is not None:
        data["condition_value"] = condition_value
    if action_type is not None:
        data["action_type"] = action_type
    if action_value is not None:
        data["action_value"] = action_value
    if is_active is not None:
        data["is_active"] = is_active

    if not data:
        return "No fields to update. Provide at least one field to change."

    result = base.api_request("PATCH", f"/filters/{filter_id}/", data=data)
    if "error" in result:
        return base.format_error(result)

    f = result
    return f"Filter updated: **{f.get('name', 'Unnamed')}** (ID: {f.get('id')})"


@mcp.tool(name="mail_delete_filter")
def delete_filter(filter_id: str) -> str:
    """
    Delete a mail filter.

    Args:
        filter_id: Filter ID to delete
    """
    result = base.api_request("DELETE", f"/filters/{filter_id}/")
    if "error" in result:
        return base.format_error(result)

    return "Filter deleted."


# ---------------------------------------------------------------------------
# Contacts
# ---------------------------------------------------------------------------


@mcp.tool(name="mail_list_contacts")
def list_contacts(search: Optional[str] = None, limit: int = 20) -> str:
    """
    List contacts.

    Args:
        search: Search by name or email
        limit: Maximum contacts
    """
    params = {"page_size": limit}
    if search:
        params["search"] = search

    result = base.api_request("GET", "/contacts/", params=params)
    if "error" in result:
        return base.format_error(result)

    contacts = (result if isinstance(result, list) else result.get("results", []))
    if not contacts:
        return "No contacts found."

    output = f"# Contacts ({len(contacts)})\n\n"
    for c in contacts:
        name = c.get("name", c.get("email", "Unknown"))
        output += f"- **{name}**\n"
        output += f"  Email: {c.get('email')}\n"
        output += f"  ID: {c.get('id')}\n"
        if c.get("company"):
            output += f"  Company: {c['company']}\n"

    return output


@mcp.tool(name="mail_create_contact")
def create_contact(
    email: str,
    name: Optional[str] = None,
    company: Optional[str] = None,
    phone: Optional[str] = None,
    notes: Optional[str] = None,
) -> str:
    """
    Create a new contact.

    Args:
        email: Contact email address
        name: Contact display name
        company: Company name
        phone: Phone number
        notes: Additional notes
    """
    data = {"email": email}
    if name:
        data["name"] = name
    if company:
        data["company"] = company
    if phone:
        data["phone"] = phone
    if notes:
        data["notes"] = notes

    result = base.api_request("POST", "/contacts/", data=data)
    if "error" in result:
        return base.format_error(result)

    c = result
    return (
        f"Contact created!\n\n"
        f"**Name**: {c.get('name', c.get('email'))}\n"
        f"**Email**: {c.get('email')}\n"
        f"**ID**: {c.get('id')}\n"
    )


@mcp.tool(name="mail_get_contact")
def get_contact(contact_id: str) -> str:
    """
    Get detailed information about a contact.

    Args:
        contact_id: Contact ID
    """
    result = base.api_request("GET", f"/contacts/{contact_id}/")
    if "error" in result:
        return base.format_error(result)

    c = result
    output = f"# Contact: {c.get('name', c.get('email', 'Unknown'))}\n\n"
    output += f"**ID**: {c.get('id')}\n"
    output += f"**Email**: {c.get('email')}\n"
    if c.get("name"):
        output += f"**Name**: {c['name']}\n"
    if c.get("company"):
        output += f"**Company**: {c['company']}\n"
    if c.get("phone"):
        output += f"**Phone**: {c['phone']}\n"
    if c.get("notes"):
        output += f"**Notes**: {c['notes']}\n"
    if c.get("created_at"):
        output += f"**Created**: {c['created_at']}\n"

    return output


@mcp.tool(name="mail_update_contact")
def update_contact(
    contact_id: str,
    name: Optional[str] = None,
    email: Optional[str] = None,
    company: Optional[str] = None,
    phone: Optional[str] = None,
    notes: Optional[str] = None,
) -> str:
    """
    Update a contact.

    Args:
        contact_id: Contact ID to update
        name: New display name
        email: New email address
        company: New company name
        phone: New phone number
        notes: New notes
    """
    data = {}
    if name is not None:
        data["name"] = name
    if email is not None:
        data["email"] = email
    if company is not None:
        data["company"] = company
    if phone is not None:
        data["phone"] = phone
    if notes is not None:
        data["notes"] = notes

    if not data:
        return "No fields to update. Provide at least one field to change."

    result = base.api_request("PATCH", f"/contacts/{contact_id}/", data=data)
    if "error" in result:
        return base.format_error(result)

    c = result
    return f"Contact updated: **{c.get('name', c.get('email', 'Unknown'))}** (ID: {c.get('id')})"


@mcp.tool(name="mail_delete_contact")
def delete_contact(contact_id: str) -> str:
    """
    Delete a contact.

    Args:
        contact_id: Contact ID to delete
    """
    result = base.api_request("DELETE", f"/contacts/{contact_id}/")
    if "error" in result:
        return base.format_error(result)

    return "Contact deleted."


@mcp.tool(name="mail_autocomplete_contacts")
def autocomplete_contacts(query: str, limit: int = 10) -> str:
    """
    Autocomplete contact search for quick recipient lookup.

    Returns matching contacts by name or email prefix.

    Args:
        query: Partial name or email to search
        limit: Maximum results
    """
    params = {"q": query, "limit": limit}

    result = base.api_request("GET", "/contacts/autocomplete/", params=params)
    if "error" in result:
        return base.format_error(result)

    contacts = (result if isinstance(result, list) else result.get("results", []))
    if not contacts:
        return f"No contacts matching '{query}'."

    output = f"# Autocomplete: '{query}' ({len(contacts)})\n\n"
    for c in contacts:
        name = c.get("name", "")
        email = c.get("email", "")
        if name:
            output += f"- **{name}** <{email}>\n"
        else:
            output += f"- {email}\n"

    return output


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    """Run the MCP server."""
    run_mcp_server(mcp, base)


if __name__ == "__main__":
    main()
