"""
Stalwart Mail Server Client

This module provides a client for interacting with Stalwart's APIs:
- Admin API: Account management (create, delete, update accounts)
- JMAP API: Email operations (mailboxes, emails, send, search)

Stalwart is a modern, Rust-based all-in-one mail server that supports:
- JMAP (JSON Meta Application Protocol) for modern email access
- IMAP for traditional email clients
- SMTP for sending/receiving
- ManageSieve for server-side filtering
"""

import logging
import httpx
from typing import Optional, List, Dict, Any
from django.conf import settings

from mail.redaction import safe_exception_text

logger = logging.getLogger(__name__)


class StalwartError(Exception):
    """Base exception for Stalwart client errors with secret-safe text."""

    def __init__(self, message='Stalwart request failed'):
        super().__init__(safe_exception_text(message))


class StalwartAdminClient:
    """
    Client for Stalwart Admin API.
    Used for account provisioning and management.
    """

    def __init__(self):
        self.base_url = settings.STALWART_ADMIN_URL
        # Support both Basic Auth (user/password) and Bearer token
        self.admin_user = getattr(settings, 'STALWART_ADMIN_USER', 'admin')
        self.admin_password = getattr(settings, 'STALWART_ADMIN_PASSWORD', '')
        self.admin_token = getattr(settings, 'STALWART_ADMIN_TOKEN', '')
        self.timeout = 30.0
        self.verify_ssl = getattr(settings, 'STALWART_VERIFY_SSL', True)

    def _get_headers(self) -> dict:
        import base64
        # Prefer Basic Auth if password is set, otherwise use Bearer token
        if self.admin_password:
            credentials = base64.b64encode(f'{self.admin_user}:{self.admin_password}'.encode()).decode()
            return {
                'Authorization': f'Basic {credentials}',
                'Content-Type': 'application/json',
            }
        return {
            'Authorization': f'Bearer {self.admin_token}',
            'Content-Type': 'application/json',
        }

    async def create_account(
        self,
        email: str,
        password: str,
        name: str = '',
        quota_mb: int = 1024
    ) -> Dict[str, Any]:
        """
        Create a new mail account in Stalwart.

        Args:
            email: Email address for the account
            password: Account password
            name: Display name
            quota_mb: Storage quota in MB

        Returns:
            Account data from Stalwart
        """
        async with httpx.AsyncClient(timeout=self.timeout, verify=self.verify_ssl) as client:
            response = await client.post(
                f'{self.base_url}/api/admin/accounts',
                headers=self._get_headers(),
                json={
                    'name': email,
                    'type': 'individual',
                    'secrets': [password],
                    'emails': [email],
                    'quota': quota_mb * 1024 * 1024,  # Convert to bytes
                    'description': name,
                }
            )

            if response.status_code not in (200, 201):
                logger.error("Failed to create account: provider error body redacted")
                raise StalwartError("Failed to create account: provider error body redacted")

            return response.json()

    async def delete_account(self, email: str) -> bool:
        """Delete a mail account from the legacy Stalwart admin API."""
        async with httpx.AsyncClient(timeout=self.timeout, verify=self.verify_ssl) as client:
            response = await client.delete(
                f'{self.base_url}/api/admin/accounts/{email}', headers=self._get_headers()
            )
            if response.status_code not in (200, 204):
                raise StalwartError('Failed to delete account: provider error body redacted')
            return True

    async def update_password(self, email: str, new_password: str) -> bool:
        """Update a password through the legacy Stalwart admin API."""
        async with httpx.AsyncClient(timeout=self.timeout, verify=self.verify_ssl) as client:
            response = await client.patch(
                f'{self.base_url}/api/admin/accounts/{email}',
                headers=self._get_headers(), json={'secrets': [new_password]},
            )
            if response.status_code != 200:
                raise StalwartError('Failed to update password: provider error body redacted')
            return True

    async def get_account(self, email: str) -> Optional[Dict[str, Any]]:
        """Fetch an account through the legacy Stalwart admin API."""
        async with httpx.AsyncClient(timeout=self.timeout, verify=self.verify_ssl) as client:
            response = await client.get(
                f'{self.base_url}/api/admin/accounts/{email}', headers=self._get_headers()
            )
            if response.status_code == 404:
                return None
            if response.status_code != 200:
                raise StalwartError('Failed to get account: provider error body redacted')
            return response.json()


class StalwartManagementClient:
    """Synchronous Stalwart v0.16 JMAP management client for provisioning."""

    CAPABILITIES = ['urn:ietf:params:jmap:core', 'urn:stalwart:jmap']

    def __init__(self):
        self.base_url = getattr(
            settings, 'STALWART_PROVISIONING_URL', settings.STALWART_ADMIN_URL,
        ).rstrip('/')
        self.user = getattr(settings, 'STALWART_PROVISIONING_USER', '')
        self.password = getattr(settings, 'STALWART_PROVISIONING_PASSWORD', '')
        self.timeout = float(getattr(settings, 'STALWART_PROVISIONING_TIMEOUT', 10))
        self.verify_ssl = getattr(settings, 'STALWART_PROVISIONING_VERIFY_SSL', True)

    def _request(self, method_calls):
        if not self.user or not self.password:
            raise StalwartError('Stalwart provisioning credentials are not configured')
        try:
            response = httpx.post(
                f'{self.base_url}/jmap',
                auth=(self.user, self.password),
                headers={'Content-Type': 'application/json'},
                json={'using': self.CAPABILITIES, 'methodCalls': method_calls},
                timeout=self.timeout,
                verify=self.verify_ssl,
            )
            response.raise_for_status()
            body = response.json()
        except Exception as exc:
            raise StalwartError(f'Stalwart management request failed: {exc}') from exc
        for name, result, _tag in body.get('methodResponses', []):
            if name == 'error':
                raise StalwartError(f'Stalwart management method failed: {result}')
        return body

    @staticmethod
    def _response(body, tag, expected_name):
        for name, result, response_tag in body.get('methodResponses', []):
            if response_tag == tag:
                if name != expected_name:
                    raise StalwartError(f'Unexpected Stalwart response method: {name}')
                return result
        raise StalwartError(f'Missing Stalwart response tag: {tag}')

    def _domain_id(self, domain):
        body = self._request([
            ['x:Domain/query', {'filter': {'name': domain}}, 'domain-query'],
        ])
        result = self._response(body, 'domain-query', 'x:Domain/query')
        ids = result.get('ids') or []
        if len(ids) != 1:
            raise StalwartError(f'Expected exactly one enabled mail domain for {domain}')
        return ids[0]

    def _account_ids(self, localpart, domain_id):
        body = self._request([
            ['x:Account/query', {
                'filter': {'name': localpart, 'domainId': domain_id},
            }, 'account-query'],
        ])
        return self._response(body, 'account-query', 'x:Account/query').get('ids') or []

    def ensure_account(self, email, description=''):
        """Return ``(account_id, created)`` after idempotently ensuring a user."""
        try:
            localpart, domain = email.lower().rsplit('@', 1)
        except ValueError as exc:
            raise StalwartError('Invalid mailbox address') from exc
        if not localpart or not domain:
            raise StalwartError('Invalid mailbox address')
        domain_id = self._domain_id(domain)
        existing = self._account_ids(localpart, domain_id)
        if existing:
            if len(existing) != 1:
                raise StalwartError('Mailbox identity is ambiguous in Stalwart')
            return existing[0], False

        create_id = 'mailbox'
        body = self._request([
            ['x:Account/set', {'create': {create_id: {
                '@type': 'User',
                'name': localpart,
                'domainId': domain_id,
                'credentials': {},
                'memberGroupIds': {},
                'roles': {'@type': 'User'},
                'permissions': {'@type': 'Inherit'},
                'quotas': {},
                'aliases': {},
                'encryptionAtRest': {'@type': 'Disabled'},
                'description': description or f'Shizuha ID mailbox {email}',
            }}}, 'account-create'],
        ])
        result = self._response(body, 'account-create', 'x:Account/set')
        created = (result.get('created') or {}).get(create_id)
        if created and created.get('id'):
            return created['id'], True
        # Concurrent idempotent creates may lose the race. Re-query before fail.
        existing = self._account_ids(localpart, domain_id)
        if len(existing) == 1:
            return existing[0], False
        raise StalwartError('Stalwart did not create the mailbox account')

    def delete_account(self, email):
        """Idempotently destroy the managed account for ``email``.

        Stalwart 0.16 removed the legacy principal REST API. Account lifecycle
        now uses the standard JMAP ``x:Account/set`` destroy operation.
        """
        try:
            localpart, domain = email.lower().rsplit('@', 1)
        except ValueError as exc:
            raise StalwartError('Invalid mailbox address') from exc
        if not localpart or not domain:
            raise StalwartError('Invalid mailbox address')
        domain_id = self._domain_id(domain)
        existing = self._account_ids(localpart, domain_id)
        if not existing:
            return False
        if len(existing) != 1:
            raise StalwartError('Mailbox identity is ambiguous in Stalwart')

        account_id = existing[0]
        body = self._request([
            ['x:Account/set', {'destroy': [account_id]}, 'account-destroy'],
        ])
        result = self._response(body, 'account-destroy', 'x:Account/set')
        if account_id in (result.get('destroyed') or []):
            return True
        # An idempotent concurrent cleanup may win after our initial query.
        remaining = self._account_ids(localpart, domain_id)
        if not remaining:
            return False
        raise StalwartError('Stalwart did not destroy the mailbox account')

class StalwartJMAPClient:
    """
    Client for Stalwart JMAP API.
    Used for email operations: reading, sending, searching, etc.
    """

    def __init__(
        self,
        user_email: str,
        user_password: Optional[str] = None,
        auth_token: Optional[str] = None,
        base_url: Optional[str] = None,
    ):
        """
        Initialize JMAP client for a specific user.

        Args:
            user_email: User's email address
            user_password: User's password (optional if using admin token)
            auth_token: Request-scoped delegated bearer (preferred when set)
            base_url: JMAP service endpoint (defaults to the legacy endpoint)
        """
        self.base_url = base_url or settings.STALWART_JMAP_URL
        self.user_email = user_email
        self.user_password = user_password
        self.auth_token = auth_token
        self.admin_token = settings.STALWART_ADMIN_TOKEN
        self.timeout = 30.0
        self.account_id = None
        self.verify_ssl = getattr(settings, 'STALWART_VERIFY_SSL', True)

    def _get_headers(self) -> dict:
        """Get authentication headers."""
        if self.auth_token:
            return {
                'Authorization': f'Bearer {self.auth_token}',
                'Content-Type': 'application/json',
            }
        elif self.user_password:
            import base64
            credentials = base64.b64encode(f'{self.user_email}:{self.user_password}'.encode()).decode()
            return {
                'Authorization': f'Basic {credentials}',
                'Content-Type': 'application/json',
            }
        else:
            # Use admin token with account context
            return {
                'Authorization': f'Bearer {self.admin_token}',
                'Content-Type': 'application/json',
            }

    async def _jmap_request(self, method_calls: List[List]) -> Dict[str, Any]:
        """
        Make a JMAP request.

        Args:
            method_calls: List of JMAP method calls

        Returns:
            JMAP response
        """
        async with httpx.AsyncClient(timeout=self.timeout, verify=self.verify_ssl) as client:
            response = await client.post(
                f'{self.base_url}/jmap',
                headers=self._get_headers(),
                json={
                    'using': [
                        'urn:ietf:params:jmap:core',
                        'urn:ietf:params:jmap:mail',
                        'urn:ietf:params:jmap:submission',
                    ],
                    'methodCalls': method_calls,
                }
            )

            if response.status_code != 200:
                logger.error("JMAP request failed: provider error body redacted")
                raise StalwartError("JMAP request failed: provider error body redacted")

            return response.json()

    async def get_session(self) -> Dict[str, Any]:
        """Get JMAP session info (account ID, capabilities)."""
        # Stalwart redirects the well-known URL to its canonical session
        # resource. Follow that relative redirect for in-cluster discovery.
        async with httpx.AsyncClient(
            timeout=self.timeout,
            verify=self.verify_ssl,
            follow_redirects=True,
        ) as client:
            response = await client.get(
                f'{self.base_url}/.well-known/jmap',
                headers=self._get_headers()
            )

            if response.status_code != 200:
                raise StalwartError("Failed to get session: provider error body redacted")

            session = response.json()
            # Cache account ID
            if 'accounts' in session:
                for account_id in session['accounts']:
                    self.account_id = account_id
                    break

            return session

    async def get_mailboxes(self) -> List[Dict[str, Any]]:
        """Get all mailboxes (folders) for the user."""
        result = await self._jmap_request([
            ['Mailbox/get', {
                'accountId': self.account_id,
                'properties': ['id', 'name', 'parentId', 'role', 'sortOrder',
                              'totalEmails', 'unreadEmails', 'totalThreads', 'unreadThreads']
            }, '0']
        ])

        return result.get('methodResponses', [[]])[0][1].get('list', [])

    async def create_mailbox(self, name: str, parent_id: Optional[str] = None) -> Dict[str, Any]:
        """Create a new mailbox (folder)."""
        create_data = {'name': name}
        if parent_id:
            create_data['parentId'] = parent_id

        result = await self._jmap_request([
            ['Mailbox/set', {
                'accountId': self.account_id,
                'create': {'new': create_data}
            }, '0']
        ])

        response = result.get('methodResponses', [[]])[0][1]
        if 'created' in response and 'new' in response['created']:
            return response['created']['new']

        raise StalwartError(f"Failed to create mailbox: {response}")

    async def delete_mailbox(self, mailbox_id: str) -> bool:
        """Delete a mailbox (folder)."""
        result = await self._jmap_request([
            ['Mailbox/set', {
                'accountId': self.account_id,
                'destroy': [mailbox_id]
            }, '0']
        ])

        response = result.get('methodResponses', [[]])[0][1]
        return mailbox_id in response.get('destroyed', [])

    async def get_emails(
        self,
        mailbox_id: Optional[str] = None,
        limit: int = 50,
        position: int = 0,
        filter_unread: Optional[bool] = None
    ) -> Dict[str, Any]:
        """
        Get emails from a mailbox.

        Returns dict with 'ids' and 'emails' lists.
        """
        filter_conditions = {}
        if mailbox_id:
            filter_conditions['inMailbox'] = mailbox_id
        if filter_unread is not None:
            filter_conditions['isUnread'] = filter_unread

        # First get email IDs
        query_result = await self._jmap_request([
            ['Email/query', {
                'accountId': self.account_id,
                'filter': filter_conditions,
                'sort': [{'property': 'receivedAt', 'isAscending': False}],
                'limit': limit,
                'position': position,
            }, 'query'],
            ['Email/get', {
                'accountId': self.account_id,
                '#ids': {'resultOf': 'query', 'name': 'Email/query', 'path': '/ids'},
                'properties': [
                    'id', 'blobId', 'threadId', 'mailboxIds',
                    'messageId', 'inReplyTo', 'references',
                    'subject', 'from', 'to', 'cc', 'bcc', 'replyTo',
                    'preview', 'keywords', 'size',
                    'sentAt', 'receivedAt', 'hasAttachment'
                ]
            }, 'get']
        ])

        responses = query_result.get('methodResponses', [])
        emails = []
        total = 0

        for response in responses:
            if response[0] == 'Email/query':
                total = response[1].get('total', 0)
            elif response[0] == 'Email/get':
                emails = response[1].get('list', [])

        return {'emails': emails, 'total': total}

    async def get_email(self, email_id: str, include_body: bool = True) -> Optional[Dict[str, Any]]:
        """Get a single email with full content."""
        properties = [
            'id', 'blobId', 'threadId', 'mailboxIds',
            'messageId', 'inReplyTo', 'references',
            'subject', 'from', 'to', 'cc', 'bcc', 'replyTo',
            'preview', 'keywords', 'size',
            'sentAt', 'receivedAt', 'hasAttachment',
            'attachments'
        ]

        if include_body:
            properties.extend(['bodyStructure', 'bodyValues', 'textBody', 'htmlBody'])

        result = await self._jmap_request([
            ['Email/get', {
                'accountId': self.account_id,
                'ids': [email_id],
                'properties': properties,
                'fetchAllBodyValues': True,
            }, '0']
        ])

        emails = result.get('methodResponses', [[]])[0][1].get('list', [])
        return emails[0] if emails else None

    async def move_emails(self, email_ids: List[str], target_mailbox_id: str, source_mailbox_id: str) -> bool:
        """Move emails to a different mailbox."""
        update = {}
        for email_id in email_ids:
            update[email_id] = {
                f'mailboxIds/{source_mailbox_id}': None,
                f'mailboxIds/{target_mailbox_id}': True,
            }

        result = await self._jmap_request([
            ['Email/set', {
                'accountId': self.account_id,
                'update': update
            }, '0']
        ])

        response = result.get('methodResponses', [[]])[0][1]
        return len(response.get('updated', {})) == len(email_ids)

    async def set_email_keywords(
        self,
        email_ids: List[str],
        is_unread: Optional[bool] = None,
        is_flagged: Optional[bool] = None,
        labels: Optional[List[str]] = None,
    ) -> bool:
        """Update email keywords (read/unread, flagged, and MAIL-64 custom labels).

        `labels` sets custom JMAP keywords (`keywords/<label> = true`). Setting a
        keyword true is idempotent, so re-applying the same filter label is a no-op.
        """
        update = {}
        for email_id in email_ids:
            changes = {}
            if is_unread is not None:
                changes['keywords/$seen'] = not is_unread
            if is_flagged is not None:
                changes['keywords/$flagged'] = is_flagged
            for label in (labels or []):
                slug = str(label).strip()
                if slug:
                    changes[f'keywords/{slug}'] = True
            update[email_id] = changes

        result = await self._jmap_request([
            ['Email/set', {
                'accountId': self.account_id,
                'update': update
            }, '0']
        ])

        response = result.get('methodResponses', [[]])[0][1]
        return len(response.get('updated', {})) == len(email_ids)

    async def delete_emails(self, email_ids: List[str]) -> bool:
        """Permanently delete emails."""
        result = await self._jmap_request([
            ['Email/set', {
                'accountId': self.account_id,
                'destroy': email_ids
            }, '0']
        ])

        response = result.get('methodResponses', [[]])[0][1]
        return len(response.get('destroyed', [])) == len(email_ids)

    async def send_email(
        self,
        to: List[Dict[str, str]],
        subject: str,
        body_text: str = '',
        body_html: str = '',
        cc: Optional[List[Dict[str, str]]] = None,
        bcc: Optional[List[Dict[str, str]]] = None,
        in_reply_to: Optional[str] = None,
        references: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Send an email.

        Args:
            to: List of {'email': str, 'name': str} dicts
            subject: Email subject
            body_text: Plain text body
            body_html: HTML body
            cc: CC recipients
            bcc: BCC recipients
            in_reply_to: Message-ID of email being replied to
            references: List of Message-IDs for threading

        Returns:
            Sent email data
        """
        email_data = {
            'mailboxIds': {},  # Will be set to Sent folder
            'from': [{'email': self.user_email}],
            'to': to,
            'subject': subject,
            'bodyValues': {},
            'textBody': [],
            'htmlBody': [],
        }

        if cc:
            email_data['cc'] = cc
        if bcc:
            email_data['bcc'] = bcc
        if in_reply_to:
            email_data['inReplyTo'] = [in_reply_to]
        if references:
            email_data['references'] = references

        # Add body parts
        if body_text:
            email_data['bodyValues']['text'] = {'value': body_text}
            email_data['textBody'].append({'partId': 'text', 'type': 'text/plain'})

        if body_html:
            email_data['bodyValues']['html'] = {'value': body_html}
            email_data['htmlBody'].append({'partId': 'html', 'type': 'text/html'})

        # Create and submit in one request
        result = await self._jmap_request([
            ['Email/set', {
                'accountId': self.account_id,
                'create': {'draft': email_data}
            }, 'create'],
            ['EmailSubmission/set', {
                'accountId': self.account_id,
                'create': {
                    'send': {
                        'emailId': '#draft',
                        'envelope': {
                            'mailFrom': {'email': self.user_email},
                            'rcptTo': [{'email': r['email']} for r in to + (cc or []) + (bcc or [])]
                        }
                    }
                },
                'onSuccessUpdateEmail': {
                    '#send': {'mailboxIds': {}}  # Move to Sent after sending
                }
            }, 'submit']
        ])

        return result

    async def search_emails(
        self,
        query: str,
        mailbox_id: Optional[str] = None,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """Search emails by text content."""
        filter_conditions = {'text': query}
        if mailbox_id:
            filter_conditions['inMailbox'] = mailbox_id

        result = await self._jmap_request([
            ['Email/query', {
                'accountId': self.account_id,
                'filter': filter_conditions,
                'sort': [{'property': 'receivedAt', 'isAscending': False}],
                'limit': limit,
            }, 'query'],
            ['Email/get', {
                'accountId': self.account_id,
                '#ids': {'resultOf': 'query', 'name': 'Email/query', 'path': '/ids'},
                'properties': [
                    'id', 'threadId', 'subject', 'from', 'to',
                    'preview', 'keywords', 'receivedAt', 'hasAttachment'
                ]
            }, 'get']
        ])

        responses = result.get('methodResponses', [])
        for response in responses:
            if response[0] == 'Email/get':
                return response[1].get('list', [])

        return []

    async def get_attachment(self, blob_id: str) -> bytes:
        """Download an attachment by blob ID."""
        async with httpx.AsyncClient(timeout=60.0, verify=self.verify_ssl) as client:
            response = await client.get(
                f'{self.base_url}/jmap/download/{self.account_id}/{blob_id}',
                headers=self._get_headers()
            )

            if response.status_code != 200:
                raise StalwartError("Failed to download attachment: provider error body redacted")

            return response.content
