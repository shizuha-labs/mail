"""
JMAP Protocol Client for Stalwart Mail Server.

JMAP (JSON Meta Application Protocol) is a modern, efficient protocol for
accessing mail, contacts, and calendars. It's designed to be efficient for
mobile and web clients.

Reference: https://jmap.io/spec-core.html
"""
import logging
from typing import Any, Optional
import httpx
from django.conf import settings

logger = logging.getLogger(__name__)


class JMAPError(Exception):
    """Base exception for JMAP errors."""
    pass


class JMAPClient:
    """
    Low-level JMAP protocol client.

    Handles JMAP session discovery, method calls, and blob operations.
    """

    CAPABILITIES = [
        'urn:ietf:params:jmap:core',
        'urn:ietf:params:jmap:mail',
    ]

    def __init__(self, base_url: str = None, auth_token: str = None):
        self.base_url = base_url or settings.STALWART_JMAP_URL
        self.auth_token = auth_token
        self._session = None
        self._account_id = None
        self.verify_ssl = getattr(settings, 'STALWART_VERIFY_SSL', True)

    def _get_headers(self) -> dict:
        """Get request headers with authentication."""
        headers = {'Content-Type': 'application/json'}
        if self.auth_token:
            headers['Authorization'] = f'Bearer {self.auth_token}'
        return headers

    async def get_session(self) -> dict:
        """
        Fetch JMAP session information.

        Returns session object with capabilities, account IDs, and endpoints.
        """
        if self._session:
            return self._session

        async with httpx.AsyncClient(verify=self.verify_ssl, follow_redirects=True) as client:
            response = await client.get(
                f'{self.base_url}/.well-known/jmap',
                headers=self._get_headers()
            )
            response.raise_for_status()
            self._session = response.json()

            # Extract primary account ID
            accounts = self._session.get('accounts', {})
            if accounts:
                self._account_id = list(accounts.keys())[0]

            return self._session

    def get_session_sync(self) -> dict:
        """Synchronous version of get_session."""
        if self._session:
            return self._session

        with httpx.Client(verify=self.verify_ssl, follow_redirects=True) as client:
            response = client.get(
                f'{self.base_url}/.well-known/jmap',
                headers=self._get_headers()
            )
            response.raise_for_status()
            self._session = response.json()

            accounts = self._session.get('accounts', {})
            if accounts:
                self._account_id = list(accounts.keys())[0]

            return self._session

    @property
    def account_id(self) -> str:
        """Get the primary account ID."""
        if not self._account_id:
            self.get_session_sync()
        return self._account_id

    def _build_request(self, method_calls: list) -> dict:
        """Build a JMAP request object."""
        return {
            'using': self.CAPABILITIES,
            'methodCalls': method_calls,
        }

    async def call(self, method_calls: list) -> dict:
        """
        Execute JMAP method calls.

        Args:
            method_calls: List of [method_name, arguments, call_id] tuples

        Returns:
            JMAP response with methodResponses
        """
        request = self._build_request(method_calls)

        async with httpx.AsyncClient(verify=self.verify_ssl) as client:
            response = await client.post(
                f'{self.base_url}/jmap',
                json=request,
                headers=self._get_headers(),
            )
            response.raise_for_status()
            return response.json()

    def call_sync(self, method_calls: list) -> dict:
        """Synchronous version of call."""
        request = self._build_request(method_calls)

        with httpx.Client(verify=self.verify_ssl) as client:
            response = client.post(
                f'{self.base_url}/jmap',
                json=request,
                headers=self._get_headers(),
            )
            response.raise_for_status()
            return response.json()

    def get_method_response(self, response: dict, call_id: str) -> Optional[dict]:
        """Extract a specific method response by call ID."""
        for method_response in response.get('methodResponses', []):
            if len(method_response) >= 3 and method_response[2] == call_id:
                return method_response[1]
        return None

    # ==================== Mailbox Methods ====================

    def mailbox_get(self, ids: list = None, properties: list = None) -> dict:
        """
        Get mailboxes (folders).

        Args:
            ids: Specific mailbox IDs to fetch (None = all)
            properties: Properties to return

        Returns:
            JMAP Mailbox/get response
        """
        args = {'accountId': self.account_id}
        if ids:
            args['ids'] = ids
        if properties:
            args['properties'] = properties

        response = self.call_sync([['Mailbox/get', args, 'mailbox-get']])
        return self.get_method_response(response, 'mailbox-get')

    def mailbox_query(self, filter_obj: dict = None, sort: list = None) -> dict:
        """Query mailboxes with optional filters."""
        args = {'accountId': self.account_id}
        if filter_obj:
            args['filter'] = filter_obj
        if sort:
            args['sort'] = sort

        response = self.call_sync([['Mailbox/query', args, 'mailbox-query']])
        return self.get_method_response(response, 'mailbox-query')

    def mailbox_set(self, create: dict = None, update: dict = None, destroy: list = None) -> dict:
        """Create, update, or delete mailboxes."""
        args = {'accountId': self.account_id}
        if create:
            args['create'] = create
        if update:
            args['update'] = update
        if destroy:
            args['destroy'] = destroy

        response = self.call_sync([['Mailbox/set', args, 'mailbox-set']])
        return self.get_method_response(response, 'mailbox-set')

    # ==================== Email Methods ====================

    def email_query(
        self,
        filter_obj: dict = None,
        sort: list = None,
        position: int = 0,
        limit: int = 50
    ) -> dict:
        """
        Query emails with filters and pagination.

        Common filters:
        - inMailbox: Mailbox ID
        - text: Full-text search
        - from: Sender address
        - to: Recipient address
        - subject: Subject contains
        - hasAttachment: Boolean
        - after/before: Date filters
        """
        args = {
            'accountId': self.account_id,
            'position': position,
            'limit': limit,
            'sort': sort or [{'property': 'receivedAt', 'isAscending': False}],
        }
        if filter_obj:
            args['filter'] = filter_obj

        response = self.call_sync([['Email/query', args, 'email-query']])
        return self.get_method_response(response, 'email-query')

    def email_get(self, ids: list, properties: list = None, fetch_body: bool = False) -> dict:
        """
        Get emails by ID.

        Args:
            ids: Email IDs to fetch
            properties: Properties to return
            fetch_body: If True, fetch body values as well
        """
        default_properties = [
            'id', 'blobId', 'threadId', 'mailboxIds', 'keywords',
            'from', 'to', 'cc', 'bcc', 'replyTo', 'subject',
            'sentAt', 'receivedAt', 'preview', 'hasAttachment',
            'attachments', 'size', 'messageId', 'inReplyTo', 'references'
        ]

        if fetch_body:
            default_properties.extend(['bodyStructure', 'textBody', 'htmlBody', 'bodyValues'])

        args = {
            'accountId': self.account_id,
            'ids': ids,
            'properties': properties or default_properties,
        }

        if fetch_body:
            args['fetchAllBodyValues'] = True

        response = self.call_sync([['Email/get', args, 'email-get']])
        return self.get_method_response(response, 'email-get')

    def email_set(
        self,
        create: dict = None,
        update: dict = None,
        destroy: list = None
    ) -> dict:
        """Create, update, or delete emails."""
        args = {'accountId': self.account_id}
        if create:
            args['create'] = create
        if update:
            args['update'] = update
        if destroy:
            args['destroy'] = destroy

        response = self.call_sync([['Email/set', args, 'email-set']])
        return self.get_method_response(response, 'email-set')

    def email_query_and_get(
        self,
        filter_obj: dict = None,
        sort: list = None,
        position: int = 0,
        limit: int = 50,
        properties: list = None
    ) -> dict:
        """
        Combined query and get in a single request (more efficient).
        """
        default_properties = [
            'id', 'blobId', 'threadId', 'mailboxIds', 'keywords',
            'from', 'to', 'cc', 'bcc', 'subject',
            'sentAt', 'receivedAt', 'preview', 'hasAttachment',
            'attachments', 'size'
        ]

        query_args = {
            'accountId': self.account_id,
            'position': position,
            'limit': limit,
            'sort': sort or [{'property': 'receivedAt', 'isAscending': False}],
        }
        if filter_obj:
            query_args['filter'] = filter_obj

        get_args = {
            'accountId': self.account_id,
            '#ids': {
                'resultOf': 'email-query',
                'name': 'Email/query',
                'path': '/ids'
            },
            'properties': properties or default_properties,
        }

        response = self.call_sync([
            ['Email/query', query_args, 'email-query'],
            ['Email/get', get_args, 'email-get'],
        ])

        return {
            'query': self.get_method_response(response, 'email-query'),
            'emails': self.get_method_response(response, 'email-get'),
        }

    # ==================== Thread Methods ====================

    def thread_get(self, ids: list) -> dict:
        """Get threads by ID."""
        args = {
            'accountId': self.account_id,
            'ids': ids,
        }

        response = self.call_sync([['Thread/get', args, 'thread-get']])
        return self.get_method_response(response, 'thread-get')

    # ==================== Email Submission ====================

    def email_submission_set(self, create: dict) -> dict:
        """
        Submit email for sending.

        The create object should reference an email created with Email/set.
        """
        args = {
            'accountId': self.account_id,
            'create': create,
        }

        response = self.call_sync([['EmailSubmission/set', args, 'submission-set']])
        return self.get_method_response(response, 'submission-set')

    # ==================== Blob Operations ====================

    def blob_download(self, blob_id: str) -> bytes:
        """Download a blob (attachment, email source, etc.)."""
        with httpx.Client(verify=self.verify_ssl) as client:
            response = client.get(
                f'{self.base_url}/jmap/download/{self.account_id}/{blob_id}',
                headers=self._get_headers(),
            )
            response.raise_for_status()
            return response.content

    def blob_upload(self, content: bytes, content_type: str = 'application/octet-stream') -> dict:
        """Upload a blob for later use in emails."""
        headers = self._get_headers()
        headers['Content-Type'] = content_type

        with httpx.Client(verify=self.verify_ssl) as client:
            response = client.post(
                f'{self.base_url}/jmap/upload/{self.account_id}',
                content=content,
                headers=headers,
            )
            response.raise_for_status()
            return response.json()

    # ==================== Search ====================

    def search_emails(self, query: str, limit: int = 50) -> dict:
        """Full-text search for emails."""
        return self.email_query_and_get(
            filter_obj={'text': query},
            limit=limit
        )
