#!/usr/bin/env python
"""
Stalwart Mail Server Admin Utilities

This script provides utilities for managing Stalwart accounts from the command line.
Stalwart runs EXTERNALLY to the k8s cluster - this script connects via Admin API.

Usage:
    python stalwart_admin.py list                    # List all accounts
    python stalwart_admin.py delete <email>         # Delete a specific account
    python stalwart_admin.py cleanup-orphans        # Delete accounts with .N suffixes
    python stalwart_admin.py create <email> [name]  # Create a new account
"""

import os
import sys
import argparse
import re
from urllib.parse import urlsplit

# Try to import from Django settings if available, otherwise use env vars
try:
    import django
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'mailmanager.settings')
    django.setup()
    from django.conf import settings
    STALWART_ADMIN_URL = getattr(settings, 'STALWART_ADMIN_URL', os.environ.get('STALWART_ADMIN_URL', ''))
    STALWART_ADMIN_USER = getattr(settings, 'STALWART_ADMIN_USER', os.environ.get('STALWART_ADMIN_USER', 'admin'))
    STALWART_ADMIN_PASSWORD = getattr(settings, 'STALWART_ADMIN_PASSWORD', os.environ.get('STALWART_ADMIN_PASSWORD', ''))
except:
    STALWART_ADMIN_URL = os.environ.get('STALWART_ADMIN_URL', '')
    STALWART_ADMIN_USER = os.environ.get('STALWART_ADMIN_USER', 'admin')
    STALWART_ADMIN_PASSWORD = os.environ.get('STALWART_ADMIN_PASSWORD', '')

import httpx


def get_client():
    """Create a client only for the explicitly configured installation endpoint."""
    try:
        parsed = urlsplit(STALWART_ADMIN_URL)
        valid = (parsed.scheme in ('http', 'https') and parsed.hostname
                 and not parsed.username and not parsed.password
                 and not parsed.query and not parsed.fragment)
        parsed.port  # Validate a malformed explicit port without exposing it.
    except (TypeError, ValueError):
        valid = False
    if not valid:
        raise RuntimeError('Configure STALWART_ADMIN_URL as an explicit HTTP(S) endpoint without credentials or query data.')
    return httpx.Client(
        timeout=30.0,
        auth=(STALWART_ADMIN_USER, STALWART_ADMIN_PASSWORD),
        headers={'Content-Type': 'application/json'}
    )


def list_accounts(client, account_type='individual'):
    """List all accounts of a given type."""
    response = client.get(f'{STALWART_ADMIN_URL}/api/principal?types={account_type}&limit=100')
    if response.status_code == 200:
        data = response.json()
        return data.get('data', {}).get('items', [])
    else:
        print(f"Error listing accounts: {response.status_code}")
        print(response.text)
        return []


def delete_account(client, email):
    """Delete an account by email."""
    response = client.delete(f'{STALWART_ADMIN_URL}/api/principal/{email}')
    if response.status_code in (200, 204):
        print(f"✓ Deleted: {email}")
        return True
    else:
        print(f"✗ Failed to delete {email}: {response.status_code}")
        print(f"  {response.text}")
        return False


def create_account(client, email, display_name=None):
    """Create a new account."""
    import secrets
    password = secrets.token_urlsafe(24)

    account_data = {
        'type': 'individual',
        'name': email,
        'secrets': [password],
        'emails': [email],
        'quota': 1024 * 1024 * 1024,  # 1GB
        'description': display_name or email.split('@')[0],
        'roles': ['user'],
    }

    response = client.post(f'{STALWART_ADMIN_URL}/api/principal', json=account_data)
    if response.status_code in (200, 201, 204):
        result = response.json()
        if 'error' not in result:
            print(f"✓ Created: {email}")
            print(f"  Password: {password}")
            return True
        else:
            print(f"✗ Failed to create {email}: {result}")
            return False
    else:
        print(f"✗ Failed to create {email}: {response.status_code}")
        print(f"  {response.text}")
        return False


def find_orphan_accounts(accounts):
    """
    Find orphan accounts that have .N suffixes (old naming scheme).

    Orphan pattern: localpart.N@domain where N is a number
    e.g., user.3@example.org is an orphan
    """
    orphan_pattern = re.compile(r'^(.+)\.\d+@(.+)$')
    orphans = []

    for acc in accounts:
        name = acc.get('name', '')
        if orphan_pattern.match(name):
            orphans.append(name)

    return orphans


def cmd_list(args):
    """List all accounts."""
    with get_client() as client:
        accounts = list_accounts(client)
        print(f"Stalwart accounts ({len(accounts)}):")
        print("-" * 50)
        for acc in accounts:
            name = acc.get('name', 'N/A')
            print(f"  {name}")


def cmd_delete(args):
    """Delete a specific account."""
    # Safety confirmation
    if not args.force:
        print(f"\n⚠️  WARNING: This will PERMANENTLY delete the mail account: {args.email}")
        print("   All emails, folders, and data will be lost forever!")
        print("   This action CANNOT be undone.\n")
        confirm = input(f"Type the email address to confirm deletion: ")
        if confirm != args.email:
            print("Confirmation failed. Aborted.")
            return

    with get_client() as client:
        delete_account(client, args.email)


def cmd_create(args):
    """Create a new account."""
    with get_client() as client:
        create_account(client, args.email, args.name)


def cmd_cleanup_orphans(args):
    """Find and delete orphan accounts with .N suffixes."""
    with get_client() as client:
        accounts = list_accounts(client)
        orphans = find_orphan_accounts(accounts)

        if not orphans:
            print("No orphan accounts found.")
            return

        print(f"Found {len(orphans)} orphan account(s):")
        for email in orphans:
            print(f"  - {email}")

        if args.dry_run:
            print("\n(Dry run - no changes made)")
            return

        if not args.force:
            confirm = input("\nDelete these accounts? [y/N] ")
            if confirm.lower() != 'y':
                print("Aborted.")
                return

        print("\nDeleting orphan accounts...")
        for email in orphans:
            delete_account(client, email)


def main():
    parser = argparse.ArgumentParser(
        description='Stalwart Mail Server Admin Utilities',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    subparsers = parser.add_subparsers(dest='command', help='Commands')

    # list command
    list_parser = subparsers.add_parser('list', help='List all accounts')
    list_parser.set_defaults(func=cmd_list)

    # delete command
    delete_parser = subparsers.add_parser('delete', help='Delete an account (DANGEROUS)')
    delete_parser.add_argument('email', help='Email address to delete')
    delete_parser.add_argument('--force', '-f', action='store_true', help='Skip confirmation prompt (DANGEROUS)')
    delete_parser.set_defaults(func=cmd_delete)

    # create command
    create_parser = subparsers.add_parser('create', help='Create a new account')
    create_parser.add_argument('email', help='Email address to create')
    create_parser.add_argument('name', nargs='?', help='Display name (optional)')
    create_parser.set_defaults(func=cmd_create)

    # cleanup-orphans command
    cleanup_parser = subparsers.add_parser('cleanup-orphans', help='Delete orphan accounts with .N suffixes')
    cleanup_parser.add_argument('--dry-run', action='store_true', help='Show what would be deleted without deleting')
    cleanup_parser.add_argument('--force', '-f', action='store_true', help='Delete without confirmation')
    cleanup_parser.set_defaults(func=cmd_cleanup_orphans)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

    if not STALWART_ADMIN_PASSWORD:
        print("Error: STALWART_ADMIN_PASSWORD not set")
        print("Set it via environment variable or Django settings")
        return 1

    return args.func(args)


if __name__ == '__main__':
    sys.exit(main() or 0)
