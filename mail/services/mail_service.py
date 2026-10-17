"""
Mail Service - High-level email operations for Shizuha Mail.

This service provides methods for email operations using the appropriate
protocol (IMAP, JMAP, or SMTP) based on account configuration.
"""

import logging
from contextlib import contextmanager
from typing import List, Dict, Any, Optional, TYPE_CHECKING
import httpx
from django.conf import settings

from .credential_service import get_credential_service, CredentialServiceError
from .imap_client import IMAPClient, IMAPError
from .smtp_client import SMTPClient, SMTPError, create_smtp_client_from_account
from .jmap_client import JMAPClient, JMAPError
from mail.redaction import redact_sensitive, safe_exception_text

if TYPE_CHECKING:
    from mail.models import MailAccount

logger = logging.getLogger(__name__)


class _BearerAuth(httpx.Auth):
    def __init__(self, token: str):
        self.token = token

    def auth_flow(self, request):
        request.headers['Authorization'] = f'Bearer {self.token}'
        yield request


class MailServiceError(Exception):
    """Base exception for mail service errors with secret-safe text."""

    def __init__(self, message='Mail service error'):
        super().__init__(safe_exception_text(message))



def _assert_account_can_send(account: 'MailAccount') -> None:
    if not getattr(account, 'is_active', False):
        raise MailServiceError('Mail account not found or inactive')

class MailService:
    """
    High-level mail service for Shizuha Mail.

    Provides protocol-agnostic email operations by using the appropriate
    client (IMAP/JMAP/SMTP) based on account configuration.
    """

    def __init__(self, oauth_access_token: str = '', delegated_mail_token: str = ''):
        self.timeout = 30.0
        self._credential_service = None
        self.oauth_access_token = oauth_access_token or ''
        self._delegated_mail_token = delegated_mail_token or ''

    @property
    def credential_service(self):
        """Lazy-load credential service."""
        if self._credential_service is None:
            self._credential_service = get_credential_service()
        return self._credential_service

    def create_imap_client(self, account: 'MailAccount') -> IMAPClient:
        """
        Create an IMAP client from a MailAccount instance.

        Args:
            account: MailAccount model instance

        Returns:
            Configured IMAPClient instance
        """
        password = ''
        if account.encrypted_incoming_password:
            password = self.credential_service.decrypt_password(
                account.encrypted_incoming_password
            )

        return IMAPClient(
            host=account.incoming_host,
            port=account.incoming_port,
            username=account.incoming_username,
            password=password,
            security=account.incoming_security,
        )

    def create_smtp_client(self, account: 'MailAccount') -> SMTPClient:
        """
        Create an SMTP client from a MailAccount instance.

        Args:
            account: MailAccount model instance

        Returns:
            Configured SMTPClient instance
        """
        oauth_access_token = self._oauth_token(account)
        encrypted_credential_present = any((
            account.encrypted_oauth_access_token,
            account.encrypted_outgoing_password,
            account.encrypted_incoming_password,
        ))
        credential_service = (
            self.credential_service if encrypted_credential_present else None
        )
        return create_smtp_client_from_account(
            account,
            credential_service,
            oauth_access_token=oauth_access_token,
        )

    def _oauth_token(self, account: 'MailAccount') -> str:
        """Resolve a bearer without persisting a managed user's live token.

        MAIL-74: managed_identity accounts exchange the caller's ID access
        token for a short-lived shizuha-mail-audience token before any
        JMAP/SMTP call. The delegated token is request-scoped only (held on
        this MailService instance for the request) and never written to the DB.
        """
        if (account.settings or {}).get('managed_identity'):
            cached = getattr(self, '_delegated_mail_token', None)
            if cached:
                return cached
            if not self.oauth_access_token:
                raise MailServiceError(
                    'Managed mail requires the authenticated request OAuth token'
                )
            from .delegated_token import DelegatedTokenError, resolve_provider_bearer
            try:
                delegated = resolve_provider_bearer(
                    subject_access_token=self.oauth_access_token,
                    managed_identity=True,
                )
            except DelegatedTokenError as exc:
                raise MailServiceError(str(exc)) from exc
            self._delegated_mail_token = delegated
            return delegated
        if account.auth_type == 'oauth2' and account.encrypted_oauth_access_token:
            return self.credential_service.decrypt_password(
                account.encrypted_oauth_access_token
            )
        return ''

    def _jmap_auth(self, account: 'MailAccount'):
        token = self._oauth_token(account)
        if token:
            return _BearerAuth(token)
        password = ''
        if account.encrypted_incoming_password:
            password = self.credential_service.decrypt_password(
                account.encrypted_incoming_password
            )
        return (account.incoming_username, password)

    @contextmanager
    def _jmap_client(self, *, verify: bool):
        """Translate transport failures into the service's public-safe error type.

        Every JMAP operation uses this boundary so DNS, connect, TLS, and read
        timeouts cannot escape into Django as an HTML 500 response.
        """
        try:
            with httpx.Client(
                timeout=self.timeout,
                verify=verify,
                follow_redirects=True,
            ) as client:
                yield client
        except httpx.HTTPError as exc:
            raise MailServiceError(
                f'JMAP provider unavailable: {safe_exception_text(exc)}'
            ) from exc

    def create_jmap_client(self, account: 'MailAccount') -> JMAPClient:
        """
        Create a JMAP client from a MailAccount instance.

        Args:
            account: MailAccount model instance

        Returns:
            Configured JMAPClient instance with Basic Auth
        """
        # Build base URL from host/port
        scheme = 'https' if account.incoming_security == 'ssl' else 'http'
        base_url = f"{scheme}://{account.incoming_host}:{account.incoming_port}"

        # For JMAP, we use HTTP Basic auth with username/password
        # The JMAPClient expects an auth_token but we need to modify it for Basic auth
        client = JMAPClient(base_url=base_url, auth_token=self._oauth_token(account))

        # Store credentials for use in _get_headers
        client._username = account.incoming_username
        client._password = ''

        return client

    def test_connection(self, account: 'MailAccount') -> Dict[str, Any]:
        """
        Test connection to an email account.

        Tests both incoming (IMAP/JMAP) and outgoing (SMTP) connections.

        Args:
            account: MailAccount model instance

        Returns:
            Dict with 'incoming' and 'outgoing' connection results
        """
        _assert_account_can_send(account)

        results = {
            'incoming': {'success': False, 'message': ''},
            'outgoing': {'success': False, 'message': ''},
        }

        # Test incoming connection
        try:
            if account.incoming_server_type == 'imap':
                imap_client = self.create_imap_client(account)
                success, message = imap_client.test_connection()
                results['incoming'] = {'success': success, 'message': redact_sensitive(message)}
            elif account.incoming_server_type == 'jmap':
                # For JMAP, try to get a session
                jmap_client = self.create_jmap_client(account)
                try:
                    session = jmap_client.get_session_sync()
                    if session:
                        results['incoming'] = {'success': True, 'message': 'JMAP connection successful'}
                    else:
                        results['incoming'] = {'success': False, 'message': 'No JMAP session returned'}
                except Exception as e:
                    results['incoming'] = {'success': False, 'message': safe_exception_text(e)}
        except Exception as e:
            results['incoming'] = {'success': False, 'message': safe_exception_text(e)}

        # Test outgoing connection
        try:
            smtp_client = self.create_smtp_client(account)
            success, message = smtp_client.test_connection()
            results['outgoing'] = {'success': success, 'message': redact_sensitive(message)}
        except Exception as e:
            results['outgoing'] = {'success': False, 'message': safe_exception_text(e)}

        return results

    def send_email(
        self,
        account: 'MailAccount',
        to_addresses: List[str],
        subject: str,
        body_text: str = '',
        body_html: str = '',
        cc_addresses: Optional[List[str]] = None,
        bcc_addresses: Optional[List[str]] = None,
        reply_to: str = None,
        in_reply_to: str = None,
        references: List[str] = None,
        attachments: List[Dict] = None,
    ) -> Dict[str, Any]:
        """
        Send an email using the account's SMTP configuration.

        Args:
            account: MailAccount to send from
            to_addresses: List of recipient email addresses
            subject: Email subject
            body_text: Plain text body
            body_html: HTML body (optional)
            cc_addresses: CC recipients (optional)
            bcc_addresses: BCC recipients (optional)
            reply_to: Reply-To address (optional)
            in_reply_to: Message-ID being replied to (optional)
            references: List of Message-IDs in thread (optional)
            attachments: List of attachment dicts (optional)

        Returns:
            Dict with send status and details

        Raises:
            MailServiceError: If sending fails
        """
        _assert_account_can_send(account)
        try:
            smtp_client = self.create_smtp_client(account)
            with smtp_client:
                result = smtp_client.send(
                    to_addresses=to_addresses,
                    subject=subject,
                    body_text=body_text,
                    body_html=body_html,
                    cc_addresses=cc_addresses,
                    bcc_addresses=bcc_addresses,
                    reply_to=reply_to,
                    in_reply_to=in_reply_to,
                    references=references,
                    attachments=attachments,
                )

            logger.info("Email sent from %s to %s", redact_sensitive(account.email_address), redact_sensitive(to_addresses))
            return {
                'status': 'sent',
                'message_id': result.get('message_id'),
                'from': account.email_address,
                'to': to_addresses,
                'cc': cc_addresses or [],
                'bcc': bcc_addresses or [],
                'subject': subject,
            }

        except SMTPError as e:
            logger.error("SMTP error sending email: %s", safe_exception_text(e))
            raise MailServiceError(f"Failed to send email: {safe_exception_text(e)}")
        except Exception as e:
            logger.error("Unexpected error sending email: %s", safe_exception_text(e))
            raise MailServiceError(f"Failed to send email: {safe_exception_text(e)}")

    def send_email_jmap(
        self,
        account: 'MailAccount',
        to_addresses: List[str],
        subject: str,
        body_text: str = '',
        body_html: str = '',
        cc_addresses: Optional[List[str]] = None,
        bcc_addresses: Optional[List[str]] = None,
        in_reply_to: str = None,
        references: List[str] = None,
    ) -> Dict[str, Any]:
        """
        Send an email via JMAP (for JMAP-enabled accounts like Stalwart).

        Args:
            account: MailAccount with JMAP configuration
            to_addresses: List of recipient email addresses
            subject: Email subject
            body_text: Plain text body
            body_html: HTML body (optional)
            cc_addresses: CC recipients (optional)
            bcc_addresses: BCC recipients (optional)
            in_reply_to: RFC Message-ID being replied to (optional)
            references: Ordered RFC Message-ID thread chain (optional)

        Returns:
            Dict with send status and email ID
        """
        _assert_account_can_send(account)
        if account.incoming_server_type != 'jmap':
            raise MailServiceError("Account is not configured for JMAP")

        password = ''
        if account.encrypted_incoming_password:
            password = self.credential_service.decrypt_password(
                account.encrypted_incoming_password
            )

        # Build JMAP URL
        scheme = 'https' if account.incoming_security == 'ssl' else 'http'
        jmap_url = f"{scheme}://{account.incoming_host}:{account.incoming_port}"
        verify_ssl = getattr(settings, 'STALWART_VERIFY_SSL', True)

        with self._jmap_client(verify=verify_ssl) as client:
            # Get JMAP session
            session_response = client.get(
                f'{jmap_url}/jmap/session',
                auth=self._jmap_auth(account)
            )

            if session_response.status_code != 200:
                raise MailServiceError("Failed to get JMAP session: provider error body redacted")

            session = session_response.json()
            mail_account_id = session.get('primaryAccounts', {}).get('urn:ietf:params:jmap:mail')
            submission_account_id = session.get('primaryAccounts', {}).get(
                'urn:ietf:params:jmap:submission', mail_account_id
            )

            if not mail_account_id:
                raise MailServiceError("Could not determine JMAP account ID")

            # Get sent mailbox ID
            mailboxes_response = client.post(
                f'{jmap_url}/jmap',
                auth=self._jmap_auth(account),
                headers={'Content-Type': 'application/json'},
                json={
                    'using': ['urn:ietf:params:jmap:core', 'urn:ietf:params:jmap:mail'],
                    'methodCalls': [
                        ['Mailbox/get', {'accountId': mail_account_id}, 'mailboxes']
                    ]
                }
            )

            sent_mailbox_id = None
            drafts_mailbox_id = None
            if mailboxes_response.status_code == 200:
                result = mailboxes_response.json()
                for method_response in result.get('methodResponses', []):
                    if method_response[0] == 'Mailbox/get':
                        for mb in method_response[1].get('list', []):
                            if mb.get('role') == 'sent':
                                sent_mailbox_id = mb.get('id')
                            elif mb.get('role') == 'drafts':
                                drafts_mailbox_id = mb.get('id')

            # Get identity
            identity_response = client.post(
                f'{jmap_url}/jmap',
                auth=self._jmap_auth(account),
                headers={'Content-Type': 'application/json'},
                json={
                    'using': ['urn:ietf:params:jmap:core', 'urn:ietf:params:jmap:submission'],
                    'methodCalls': [
                        ['Identity/get', {'accountId': submission_account_id}, 'identities']
                    ]
                }
            )

            identity_id = None
            if identity_response.status_code == 200:
                result = identity_response.json()
                for method_response in result.get('methodResponses', []):
                    if method_response[0] == 'Identity/get':
                        identities = method_response[1].get('list', [])
                        for ident in identities:
                            if ident.get('email') == account.email_address:
                                identity_id = ident.get('id')
                                break
                        if not identity_id and identities:
                            identity_id = identities[0].get('id')

            # MAIL-53: EmailSubmission REQUIRES identityId (RFC 8621 §7.5).
            # Fail EARLY with a specific error before creating any message, so
            # a missing identity can never leave a phantom copy behind.
            if not identity_id:
                raise MailServiceError(
                    f"No JMAP identity available for {account.email_address}; "
                    "the account cannot submit mail (create an identity on the provider)"
                )

            # Build email body
            body_values = {}
            text_body = []
            html_body = []

            if body_text:
                body_values['text'] = {'value': body_text, 'isEncodingProblem': False, 'isTruncated': False}
                text_body = [{'partId': 'text', 'type': 'text/plain'}]

            if body_html:
                body_values['html'] = {'value': body_html, 'isEncodingProblem': False, 'isTruncated': False}
                html_body = [{'partId': 'html', 'type': 'text/html'}]

            # Build recipients
            to_list = [{'email': addr} for addr in to_addresses]
            cc_list = [{'email': addr} for addr in (cc_addresses or [])]
            bcc_list = [{'email': addr} for addr in (bcc_addresses or [])]

            # MAIL-53: create the message as a DRAFT. It moves to Sent only
            # when the submission actually succeeds (onSuccessUpdateEmail),
            # so the API response and the Sent folder always agree — no more
            # 500-with-message-in-Sent, no phantom sends.
            mailbox_ids = {}
            if drafts_mailbox_id:
                mailbox_ids[drafts_mailbox_id] = True
            elif sent_mailbox_id:  # legacy servers without a drafts role
                mailbox_ids[sent_mailbox_id] = True

            email_create = {
                'keywords': {'$draft': True},
                'mailboxIds': mailbox_ids,
                'from': [{'email': account.email_address, 'name': account.from_name or ''}],
                'to': to_list,
                'subject': subject,
                'bodyValues': body_values,
                'textBody': text_body if text_body else None,
                'htmlBody': html_body if html_body else None,
            }

            if cc_list:
                email_create['cc'] = cc_list
            if bcc_list:
                email_create['bcc'] = bcc_list
            if in_reply_to:
                email_create['inReplyTo'] = [in_reply_to]
            if references:
                email_create['references'] = references

            email_create = {k: v for k, v in email_create.items() if v is not None}

            submission_create = {
                'emailId': '#draft',
                'envelope': {
                    'mailFrom': {'email': account.email_address},
                    'rcptTo': [{'email': addr} for addr in to_addresses + (cc_addresses or []) + (bcc_addresses or [])]
                }
            }

            if identity_id:
                submission_create['identityId'] = identity_id

            # Send via JMAP
            response = client.post(
                f'{jmap_url}/jmap',
                auth=self._jmap_auth(account),
                headers={'Content-Type': 'application/json'},
                json={
                    'using': [
                        'urn:ietf:params:jmap:core',
                        'urn:ietf:params:jmap:mail',
                        'urn:ietf:params:jmap:submission'
                    ],
                    'methodCalls': [
                        ['Email/set', {
                            'accountId': mail_account_id,
                            'create': {'draft': email_create}
                        }, 'email'],
                        ['EmailSubmission/set', {
                            'accountId': submission_account_id,
                            'create': {'submission': submission_create},
                            # MAIL-53: server-side atomic move Drafts→Sent that
                            # fires ONLY if the submission is created.
                            'onSuccessUpdateEmail': {
                                '#submission': _on_success_update(sent_mailbox_id, drafts_mailbox_id),
                            },
                        }, 'submit']
                    ]
                }
            )

            if response.status_code != 200:
                raise MailServiceError("Failed to send email: provider error body redacted")

            result = response.json()
            email_id = None
            errors = []

            for method_response in result.get('methodResponses', []):
                if method_response[0] == 'Email/set':
                    data = method_response[1]
                    created = data.get('created', {})
                    if 'draft' in created:
                        email_id = created['draft'].get('id')
                    not_created = data.get('notCreated', {})
                    if not_created:
                        errors.append(f"Email/set: {not_created}")

                elif method_response[0] == 'EmailSubmission/set':
                    data = method_response[1]
                    not_created = data.get('notCreated', {})
                    if not_created:
                        errors.append(f"EmailSubmission/set: {not_created}")

                elif method_response[0] == 'error':
                    errors.append(str(method_response[1]))

            if errors:
                # MAIL-53: name the failing JMAP method — and state where the
                # message ended up, so there is never ambiguous half-sent state
                # (a failed submission leaves the copy in Drafts, not Sent).
                raise MailServiceError(
                    "Send failed (message left in Drafts, not sent): "
                    f"{redact_sensitive('; '.join(errors))}"
                )

            logger.info("Email sent via JMAP from %s", redact_sensitive(account.email_address))

            return {
                'status': 'sent',
                'email_id': email_id,
                'from': account.email_address,
                'to': to_addresses,
                'cc': cc_addresses or [],
                'bcc': bcc_addresses or [],
                'subject': subject,
            }

    def sync_folders(self, account: 'MailAccount') -> List[Dict[str, Any]]:
        """
        Sync folders/mailboxes from the mail server.

        Args:
            account: MailAccount to sync folders for

        Returns:
            List of folder data
        """
        if account.incoming_server_type == 'imap':
            return self._sync_folders_imap(account)
        elif account.incoming_server_type == 'jmap':
            return self._sync_folders_jmap(account)
        else:
            raise MailServiceError(f"Unsupported server type: {account.incoming_server_type}")

    def _sync_folders_imap(self, account: 'MailAccount') -> List[Dict[str, Any]]:
        """Sync folders via IMAP."""
        imap_client = self.create_imap_client(account)

        try:
            with imap_client:
                folders = imap_client.list_folders()
                return [
                    {
                        'name': f.name,
                        'role': imap_client.get_folder_role(f.name),
                        'total_messages': f.total_messages,
                        'unread_messages': f.unread_messages,
                        'delimiter': f.delimiter,
                    }
                    for f in folders
                ]
        except IMAPError as e:
            raise MailServiceError(f"Failed to sync folders: {safe_exception_text(e)}")

    def _sync_folders_jmap(self, account: 'MailAccount') -> List[Dict[str, Any]]:
        """Sync folders via JMAP."""
        password = ''
        if account.encrypted_incoming_password:
            password = self.credential_service.decrypt_password(
                account.encrypted_incoming_password
            )

        scheme = 'https' if account.incoming_security == 'ssl' else 'http'
        jmap_url = f"{scheme}://{account.incoming_host}:{account.incoming_port}"
        verify_ssl = getattr(settings, 'STALWART_VERIFY_SSL', True)

        with self._jmap_client(verify=verify_ssl) as client:
            session_response = client.get(
                f'{jmap_url}/jmap/session',
                auth=self._jmap_auth(account)
            )

            if session_response.status_code != 200:
                raise MailServiceError("Failed to get JMAP session: provider error body redacted")

            session = session_response.json()
            mail_account_id = session.get('primaryAccounts', {}).get('urn:ietf:params:jmap:mail')

            if not mail_account_id:
                raise MailServiceError("Could not determine JMAP account ID")

            response = client.post(
                f'{jmap_url}/jmap',
                auth=self._jmap_auth(account),
                headers={'Content-Type': 'application/json'},
                json={
                    'using': ['urn:ietf:params:jmap:core', 'urn:ietf:params:jmap:mail'],
                    'methodCalls': [
                        ['Mailbox/get', {'accountId': mail_account_id}, 'mailboxes']
                    ]
                }
            )

            if response.status_code != 200:
                raise MailServiceError("Failed to get mailboxes: provider error body redacted")

            result = response.json()
            folders = []
            for method_response in result.get('methodResponses', []):
                if method_response[0] == 'Mailbox/get':
                    for mb in method_response[1].get('list', []):
                        folders.append({
                            'jmap_mailbox_id': mb.get('id'),
                            'name': mb.get('name'),
                            'role': mb.get('role', 'custom'),
                            'total_messages': mb.get('totalEmails', 0),
                            'unread_messages': mb.get('unreadEmails', 0),
                            'parent_id': mb.get('parentId'),
                        })

            return folders

    def update_folder(self, account: 'MailAccount', folder_id: str, update: Dict[str, Any]) -> Dict[str, Any]:
        """Update a provider mailbox/folder."""
        if account.incoming_server_type != 'jmap':
            raise MailServiceError("Folder update is not supported for this account type")

        password = ''
        if account.encrypted_incoming_password:
            password = self.credential_service.decrypt_password(account.encrypted_incoming_password)

        scheme = 'https' if account.incoming_security == 'ssl' else 'http'
        jmap_url = f"{scheme}://{account.incoming_host}:{account.incoming_port}"
        verify_ssl = getattr(settings, 'STALWART_VERIFY_SSL', True)

        with self._jmap_client(verify=verify_ssl) as client:
            session_response = client.get(f'{jmap_url}/jmap/session', auth=self._jmap_auth(account))
            if session_response.status_code != 200:
                raise MailServiceError("Failed to get JMAP session: provider error body redacted")

            session = session_response.json()
            mail_account_id = session.get('primaryAccounts', {}).get('urn:ietf:params:jmap:mail')
            if not mail_account_id:
                raise MailServiceError("Could not determine JMAP account ID")

            response = client.post(
                f'{jmap_url}/jmap',
                auth=self._jmap_auth(account),
                headers={'Content-Type': 'application/json'},
                json={
                    'using': ['urn:ietf:params:jmap:core', 'urn:ietf:params:jmap:mail'],
                    'methodCalls': [
                        ['Mailbox/set', {
                            'accountId': mail_account_id,
                            'update': {folder_id: update},
                        }, 'mailbox-update']
                    ],
                },
            )
            if response.status_code != 200:
                raise MailServiceError("Failed to update mailbox: provider error body redacted")

            result = response.json()
            for method_response in result.get('methodResponses', []):
                if method_response[0] == 'Mailbox/set':
                    data = method_response[1]
                    if folder_id in data.get('updated', {}):
                        return data
                    if folder_id in data.get('notUpdated', {}):
                        raise MailServiceError("Failed to update mailbox: provider rejected update")
            raise MailServiceError("Failed to update mailbox: malformed provider response")

    def delete_folder(self, account: 'MailAccount', folder_id: str) -> Dict[str, Any]:
        """Delete a provider mailbox/folder."""
        if account.incoming_server_type != 'jmap':
            raise MailServiceError("Folder delete is not supported for this account type")

        password = ''
        if account.encrypted_incoming_password:
            password = self.credential_service.decrypt_password(account.encrypted_incoming_password)

        scheme = 'https' if account.incoming_security == 'ssl' else 'http'
        jmap_url = f"{scheme}://{account.incoming_host}:{account.incoming_port}"
        verify_ssl = getattr(settings, 'STALWART_VERIFY_SSL', True)

        with self._jmap_client(verify=verify_ssl) as client:
            session_response = client.get(f'{jmap_url}/jmap/session', auth=self._jmap_auth(account))
            if session_response.status_code != 200:
                raise MailServiceError("Failed to get JMAP session: provider error body redacted")

            session = session_response.json()
            mail_account_id = session.get('primaryAccounts', {}).get('urn:ietf:params:jmap:mail')
            if not mail_account_id:
                raise MailServiceError("Could not determine JMAP account ID")

            response = client.post(
                f'{jmap_url}/jmap',
                auth=self._jmap_auth(account),
                headers={'Content-Type': 'application/json'},
                json={
                    'using': ['urn:ietf:params:jmap:core', 'urn:ietf:params:jmap:mail'],
                    'methodCalls': [
                        ['Mailbox/set', {
                            'accountId': mail_account_id,
                            'destroy': [folder_id],
                        }, 'mailbox-delete']
                    ],
                },
            )
            if response.status_code != 200:
                raise MailServiceError("Failed to delete mailbox: provider error body redacted")

            result = response.json()
            for method_response in result.get('methodResponses', []):
                if method_response[0] == 'Mailbox/set':
                    data = method_response[1]
                    if folder_id in data.get('destroyed', []):
                        return data
                    if folder_id in data.get('notDestroyed', {}):
                        raise MailServiceError("Failed to delete mailbox: provider rejected delete")
            raise MailServiceError("Failed to delete mailbox: malformed provider response")

    def sync_messages(
        self,
        account: 'MailAccount',
        folder_id: str,
        limit: int = 50,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """
        Sync messages from a folder.

        Args:
            account: MailAccount to sync from
            folder_id: Folder ID (IMAP folder name or JMAP mailbox ID)
            limit: Maximum number of messages to fetch
            offset: Number of messages to skip

        Returns:
            Dict with 'messages' list and 'total' count
        """
        if account.incoming_server_type == 'imap':
            return self._sync_messages_imap(account, folder_id, limit, offset)
        elif account.incoming_server_type == 'jmap':
            return self._sync_messages_jmap(account, folder_id, limit)
        else:
            raise MailServiceError(f"Unsupported server type: {account.incoming_server_type}")

    def _sync_messages_imap(
        self,
        account: 'MailAccount',
        folder_name: str,
        limit: int,
        offset: int,
    ) -> Dict[str, Any]:
        """Sync messages via IMAP."""
        imap_client = self.create_imap_client(account)

        try:
            with imap_client:
                messages = imap_client.fetch_messages(
                    folder_name=folder_name,
                    limit=limit,
                    offset=offset,
                )

                return {
                    'messages': [
                        {
                            'uid': m.uid,
                            'message_id': m.message_id,
                            'subject': m.subject,
                            'from_address': m.from_address,
                            'from_name': m.from_name,
                            'to_addresses': m.to_addresses,
                            'cc_addresses': m.cc_addresses,
                            'date_sent': m.date_sent.isoformat() if m.date_sent else None,
                            'preview': m.preview,
                            'size_bytes': m.size_bytes,
                            'is_unread': '\\Seen' not in m.flags,
                            'is_flagged': '\\Flagged' in m.flags,
                            'has_attachments': m.has_attachments,
                        }
                        for m in messages
                    ],
                    'total': len(messages),  # IMAP doesn't provide total easily
                }
        except IMAPError as e:
            raise MailServiceError(f"Failed to sync messages: {safe_exception_text(e)}")

    def _sync_messages_jmap(
        self,
        account: 'MailAccount',
        mailbox_id: str,
        limit: int,
    ) -> Dict[str, Any]:
        """Sync messages via JMAP."""
        password = ''
        if account.encrypted_incoming_password:
            password = self.credential_service.decrypt_password(
                account.encrypted_incoming_password
            )

        scheme = 'https' if account.incoming_security == 'ssl' else 'http'
        jmap_url = f"{scheme}://{account.incoming_host}:{account.incoming_port}"
        verify_ssl = getattr(settings, 'STALWART_VERIFY_SSL', True)

        with self._jmap_client(verify=verify_ssl) as client:
            session_response = client.get(
                f'{jmap_url}/jmap/session',
                auth=self._jmap_auth(account)
            )

            if session_response.status_code != 200:
                raise MailServiceError("Failed to get JMAP session: provider error body redacted")

            session = session_response.json()
            mail_account_id = session.get('primaryAccounts', {}).get('urn:ietf:params:jmap:mail')

            response = client.post(
                f'{jmap_url}/jmap',
                auth=self._jmap_auth(account),
                headers={'Content-Type': 'application/json'},
                json={
                    'using': ['urn:ietf:params:jmap:core', 'urn:ietf:params:jmap:mail'],
                    'methodCalls': [
                        ['Email/query', {
                            'accountId': mail_account_id,
                            'filter': {'inMailbox': mailbox_id},
                            'sort': [{'property': 'receivedAt', 'isAscending': False}],
                            'limit': limit,
                            # JMAP only includes the response `total` when this
                            # is requested; without it Stalwart can return IDs
                            # while the API reports count=0 (MAIL-54).
                            'calculateTotal': True,
                        }, 'query'],
                        ['Email/get', {
                            'accountId': mail_account_id,
                            '#ids': {'resultOf': 'query', 'name': 'Email/query', 'path': '/ids'},
                            'properties': [
                                'id', 'threadId', 'subject', 'from', 'to', 'cc', 'bcc',
                                'receivedAt', 'sentAt', 'preview', 'hasAttachment',
                                'keywords', 'size'
                            ]
                        }, 'emails']
                    ]
                }
            )

            if response.status_code != 200:
                raise MailServiceError("Failed to get emails: provider error body redacted")

            result = response.json()
            messages = []
            total = None

            for method_response in result.get('methodResponses', []):
                if method_response[0] == 'Email/query':
                    total = method_response[1].get('total')
                elif method_response[0] == 'Email/get':
                    for email in method_response[1].get('list', []):
                        keywords = email.get('keywords') or {}
                        from_list = email.get('from') or []
                        messages.append({
                            'jmap_email_id': email.get('id'),
                            'jmap_thread_id': email.get('threadId'),
                            'subject': email.get('subject', ''),
                            'from_address': from_list[0].get('email', '') if from_list else '',
                            'from_name': from_list[0].get('name', '') if from_list else '',
                            'to_addresses': email.get('to') or [],
                            'cc_addresses': email.get('cc') or [],
                            'date_sent': email.get('sentAt'),
                            'date_received': email.get('receivedAt'),
                            'preview': email.get('preview', ''),
                            'size_bytes': email.get('size', 0),
                            'is_unread': '$seen' not in keywords,
                            'is_flagged': '$flagged' in keywords,
                            'has_attachments': email.get('hasAttachment', False),
                        })

            if total is None:
                total = len(messages)
            else:
                total = max(int(total), len(messages))

            return {'messages': messages, 'total': total}

    def get_message(
        self,
        account: 'MailAccount',
        message_id: str,
        folder_id: str = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Get a single message with full content.

        Args:
            account: MailAccount to fetch from
            message_id: Message ID (IMAP UID or JMAP email ID)
            folder_id: Folder ID (required for IMAP)

        Returns:
            Full message data including body
        """
        if account.incoming_server_type == 'imap':
            if not folder_id:
                raise MailServiceError("folder_id required for IMAP")
            return self._get_message_imap(account, folder_id, message_id)
        elif account.incoming_server_type == 'jmap':
            return self._get_message_jmap(account, message_id)
        else:
            raise MailServiceError(f"Unsupported server type: {account.incoming_server_type}")

    def _get_message_imap(
        self,
        account: 'MailAccount',
        folder_name: str,
        uid: str,
    ) -> Optional[Dict[str, Any]]:
        """Get a message via IMAP."""
        imap_client = self.create_imap_client(account)

        try:
            with imap_client:
                raw = imap_client.fetch_message(folder_name, uid, include_body=True)
                if not raw:
                    return None

                from_info = raw.get('from', {})
                return {
                    'id': raw.get('uid', uid),
                    'message_id': raw.get('message_id', ''),
                    'subject': raw.get('subject', ''),
                    'from_address': from_info.get('email', '') if isinstance(from_info, dict) else str(from_info),
                    'from_name': from_info.get('name', '') if isinstance(from_info, dict) else '',
                    'to_addresses': [a.get('email', '') if isinstance(a, dict) else str(a) for a in (raw.get('to') or [])],
                    'cc_addresses': [a.get('email', '') if isinstance(a, dict) else str(a) for a in (raw.get('cc') or [])],
                    'bcc_addresses': [a.get('email', '') if isinstance(a, dict) else str(a) for a in (raw.get('bcc') or [])],
                    'reply_to': raw.get('reply_to', {}).get('email', '') if isinstance(raw.get('reply_to'), dict) else str(raw.get('reply_to', '')),
                    'date_sent': raw.get('date', ''),
                    'date_received': raw.get('date', ''),
                    'preview': (raw.get('body_text', '') or '')[:200],
                    'is_unread': True,  # IMAP doesn't return this from fetch_message
                    'is_flagged': False,
                    'has_attachments': len(raw.get('attachments') or []) > 0,
                    'body_html': raw.get('body_html', ''),
                    'body_text': raw.get('body_text', ''),
                    'attachments': raw.get('attachments') or [],
                    'account_id': str(account.id),
                    'in_reply_to': raw.get('in_reply_to', ''),
                    'references': raw.get('references') or [],
                }
        except IMAPError as e:
            raise MailServiceError(f"Failed to get message: {safe_exception_text(e)}")

    def _get_message_jmap(
        self,
        account: 'MailAccount',
        email_id: str,
    ) -> Optional[Dict[str, Any]]:
        """Get a message via JMAP."""
        password = ''
        if account.encrypted_incoming_password:
            password = self.credential_service.decrypt_password(
                account.encrypted_incoming_password
            )

        scheme = 'https' if account.incoming_security == 'ssl' else 'http'
        jmap_url = f"{scheme}://{account.incoming_host}:{account.incoming_port}"
        verify_ssl = getattr(settings, 'STALWART_VERIFY_SSL', True)

        with self._jmap_client(verify=verify_ssl) as client:
            session_response = client.get(
                f'{jmap_url}/jmap/session',
                auth=self._jmap_auth(account)
            )

            if session_response.status_code != 200:
                raise MailServiceError("Failed to get JMAP session: provider error body redacted")

            session = session_response.json()
            mail_account_id = session.get('primaryAccounts', {}).get('urn:ietf:params:jmap:mail')

            response = client.post(
                f'{jmap_url}/jmap',
                auth=self._jmap_auth(account),
                headers={'Content-Type': 'application/json'},
                json={
                    'using': ['urn:ietf:params:jmap:core', 'urn:ietf:params:jmap:mail'],
                    'methodCalls': [
                        ['Email/get', {
                            'accountId': mail_account_id,
                            'ids': [email_id],
                            'properties': [
                                'id', 'threadId', 'subject', 'from', 'to', 'cc', 'bcc',
                                'receivedAt', 'sentAt', 'preview', 'hasAttachment',
                                'keywords', 'size', 'bodyStructure', 'bodyValues',
                                'textBody', 'htmlBody', 'attachments'
                            ],
                            'fetchAllBodyValues': True
                        }, 'email']
                    ]
                }
            )

            if response.status_code != 200:
                raise MailServiceError("Failed to get email: provider error body redacted")

            result = response.json()
            for method_response in result.get('methodResponses', []):
                if method_response[0] == 'Email/get':
                    emails = method_response[1].get('list', [])
                    if not emails:
                        return None
                    raw = emails[0]

                    # Extract body content from JMAP bodyValues + partId references
                    body_values = raw.get('bodyValues') or {}
                    body_html = ''
                    for part in (raw.get('htmlBody') or []):
                        part_id = part.get('partId')
                        if part_id and part_id in body_values:
                            body_html += body_values[part_id].get('value', '')
                    body_text = ''
                    for part in (raw.get('textBody') or []):
                        part_id = part.get('partId')
                        if part_id and part_id in body_values:
                            body_text += body_values[part_id].get('value', '')

                    # Extract attachments
                    attachments = []
                    for att in (raw.get('attachments') or []):
                        attachments.append({
                            'id': att.get('blobId', ''),
                            'filename': att.get('name', 'attachment'),
                            'content_type': att.get('type', 'application/octet-stream'),
                            'size_bytes': att.get('size', 0),
                        })

                    from_list = raw.get('from') or []
                    keywords = raw.get('keywords') or {}

                    return {
                        'id': raw.get('id'),
                        'thread_id': raw.get('threadId'),
                        'message_id': raw.get('messageId', [None])[0] if isinstance(raw.get('messageId'), list) else raw.get('messageId', ''),
                        'subject': raw.get('subject', ''),
                        'from_address': from_list[0].get('email', '') if from_list else '',
                        'from_name': from_list[0].get('name', '') if from_list else '',
                        'to_addresses': [a.get('email', '') for a in (raw.get('to') or [])],
                        'cc_addresses': [a.get('email', '') for a in (raw.get('cc') or [])],
                        'bcc_addresses': [a.get('email', '') for a in (raw.get('bcc') or [])],
                        'reply_to': (raw.get('replyTo') or [{}])[0].get('email', '') if raw.get('replyTo') else '',
                        'date_sent': raw.get('sentAt'),
                        'date_received': raw.get('receivedAt'),
                        'preview': raw.get('preview', ''),
                        'is_unread': '$seen' not in keywords,
                        'is_flagged': '$flagged' in keywords,
                        'has_attachments': raw.get('hasAttachment', False),
                        'body_html': body_html,
                        'body_text': body_text,
                        'attachments': attachments,
                        'account_id': str(account.id),
                        'in_reply_to': raw.get('inReplyTo', [None])[0] if isinstance(raw.get('inReplyTo'), list) else raw.get('inReplyTo', ''),
                        'references': raw.get('references') or [],
                    }

            return None

    def move_messages(
        self,
        account: 'MailAccount',
        message_ids: List[str],
        from_folder_id: str,
        to_folder_id: str,
    ) -> Dict[str, Any]:
        """
        Move messages from one folder to another.

        Args:
            account: MailAccount
            message_ids: List of message IDs to move
            from_folder_id: Source folder ID
            to_folder_id: Destination folder ID

        Returns:
            Dict with move results
        """
        if account.incoming_server_type == 'imap':
            return self._move_messages_imap(account, message_ids, from_folder_id, to_folder_id)
        elif account.incoming_server_type == 'jmap':
            return self._move_messages_jmap(account, message_ids, from_folder_id, to_folder_id)
        else:
            raise MailServiceError(f"Unsupported server type: {account.incoming_server_type}")

    def _move_messages_imap(
        self,
        account: 'MailAccount',
        uids: List[str],
        from_folder: str,
        to_folder: str,
    ) -> Dict[str, Any]:
        """Move messages via IMAP."""
        imap_client = self.create_imap_client(account)

        try:
            with imap_client:
                imap_client.move_messages(from_folder, uids, to_folder)
                return {'moved': uids, 'failed': []}
        except IMAPError as e:
            raise MailServiceError(f"Failed to move messages: {safe_exception_text(e)}")

    def _move_messages_jmap(
        self,
        account: 'MailAccount',
        email_ids: List[str],
        from_mailbox_id: str,
        to_mailbox_id: str,
    ) -> Dict[str, Any]:
        """Move messages via JMAP."""
        password = ''
        if account.encrypted_incoming_password:
            password = self.credential_service.decrypt_password(
                account.encrypted_incoming_password
            )

        scheme = 'https' if account.incoming_security == 'ssl' else 'http'
        jmap_url = f"{scheme}://{account.incoming_host}:{account.incoming_port}"
        verify_ssl = getattr(settings, 'STALWART_VERIFY_SSL', True)

        with self._jmap_client(verify=verify_ssl) as client:
            session_response = client.get(
                f'{jmap_url}/jmap/session',
                auth=self._jmap_auth(account)
            )

            if session_response.status_code != 200:
                raise MailServiceError("Failed to get JMAP session: provider error body redacted")

            session = session_response.json()
            mail_account_id = session.get('primaryAccounts', {}).get('urn:ietf:params:jmap:mail')

            update = {}
            for email_id in email_ids:
                update[email_id] = {
                    f'mailboxIds/{from_mailbox_id}': None,
                    f'mailboxIds/{to_mailbox_id}': True,
                }

            response = client.post(
                f'{jmap_url}/jmap',
                auth=self._jmap_auth(account),
                headers={'Content-Type': 'application/json'},
                json={
                    'using': ['urn:ietf:params:jmap:core', 'urn:ietf:params:jmap:mail'],
                    'methodCalls': [
                        ['Email/set', {
                            'accountId': mail_account_id,
                            'update': update
                        }, 'move']
                    ]
                }
            )

            if response.status_code != 200:
                raise MailServiceError("Failed to move emails: provider error body redacted")

            result = response.json()
            for method_response in result.get('methodResponses', []):
                if method_response[0] == 'Email/set':
                    data = method_response[1]
                    return {
                        'moved': list(data.get('updated', {}).keys()),
                        'failed': data.get('notUpdated', {}),
                    }

            return {'moved': [], 'failed': {}}

    def delete_messages(
        self,
        account: 'MailAccount',
        message_ids: List[str],
        folder_id: str = None,
    ) -> Dict[str, Any]:
        """
        Permanently delete messages.

        Args:
            account: MailAccount
            message_ids: List of message IDs to delete
            folder_id: Folder ID (required for IMAP)

        Returns:
            Dict with delete results
        """
        if account.incoming_server_type == 'imap':
            if not folder_id:
                raise MailServiceError("folder_id required for IMAP")
            return self._delete_messages_imap(account, message_ids, folder_id)
        elif account.incoming_server_type == 'jmap':
            return self._delete_messages_jmap(account, message_ids)
        else:
            raise MailServiceError(f"Unsupported server type: {account.incoming_server_type}")

    def _delete_messages_imap(
        self,
        account: 'MailAccount',
        uids: List[str],
        folder_name: str,
    ) -> Dict[str, Any]:
        """Delete messages via IMAP."""
        imap_client = self.create_imap_client(account)

        try:
            with imap_client:
                imap_client.delete_messages(folder_name, uids)
                return {'deleted': uids, 'failed': []}
        except IMAPError as e:
            raise MailServiceError(f"Failed to delete messages: {safe_exception_text(e)}")

    def _delete_messages_jmap(
        self,
        account: 'MailAccount',
        email_ids: List[str],
    ) -> Dict[str, Any]:
        """Delete messages via JMAP."""
        password = ''
        if account.encrypted_incoming_password:
            password = self.credential_service.decrypt_password(
                account.encrypted_incoming_password
            )

        scheme = 'https' if account.incoming_security == 'ssl' else 'http'
        jmap_url = f"{scheme}://{account.incoming_host}:{account.incoming_port}"
        verify_ssl = getattr(settings, 'STALWART_VERIFY_SSL', True)

        with self._jmap_client(verify=verify_ssl) as client:
            session_response = client.get(
                f'{jmap_url}/jmap/session',
                auth=self._jmap_auth(account)
            )

            if session_response.status_code != 200:
                raise MailServiceError("Failed to get JMAP session: provider error body redacted")

            session = session_response.json()
            mail_account_id = session.get('primaryAccounts', {}).get('urn:ietf:params:jmap:mail')

            response = client.post(
                f'{jmap_url}/jmap',
                auth=self._jmap_auth(account),
                headers={'Content-Type': 'application/json'},
                json={
                    'using': ['urn:ietf:params:jmap:core', 'urn:ietf:params:jmap:mail'],
                    'methodCalls': [
                        ['Email/set', {
                            'accountId': mail_account_id,
                            'destroy': email_ids
                        }, 'delete']
                    ]
                }
            )

            if response.status_code != 200:
                raise MailServiceError("Failed to delete emails: provider error body redacted")

            result = response.json()
            for method_response in result.get('methodResponses', []):
                if method_response[0] == 'Email/set':
                    data = method_response[1]
                    return {
                        'deleted': data.get('destroyed', []),
                        'failed': data.get('notDestroyed', {}),
                    }

            return {'deleted': [], 'failed': {}}

    def set_message_flags(
        self,
        account: 'MailAccount',
        message_ids: List[str],
        folder_id: str = None,
        is_unread: Optional[bool] = None,
        is_flagged: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """
        Update message flags.

        Args:
            account: MailAccount
            message_ids: List of message IDs to update
            folder_id: Folder ID (required for IMAP)
            is_unread: Set to True for unread, False for read
            is_flagged: Set to True to flag, False to unflag

        Returns:
            Dict with update results
        """
        if account.incoming_server_type == 'imap':
            if not folder_id:
                raise MailServiceError("folder_id required for IMAP")
            return self._set_flags_imap(account, message_ids, folder_id, is_unread, is_flagged)
        elif account.incoming_server_type == 'jmap':
            return self._set_flags_jmap(account, message_ids, is_unread, is_flagged)
        else:
            raise MailServiceError(f"Unsupported server type: {account.incoming_server_type}")

    def _set_flags_imap(
        self,
        account: 'MailAccount',
        uids: List[str],
        folder_name: str,
        is_unread: Optional[bool],
        is_flagged: Optional[bool],
    ) -> Dict[str, Any]:
        """Set flags via IMAP."""
        imap_client = self.create_imap_client(account)

        try:
            with imap_client:
                add_flags = []
                remove_flags = []

                if is_unread is not None:
                    if is_unread:
                        remove_flags.append('\\Seen')
                    else:
                        add_flags.append('\\Seen')

                if is_flagged is not None:
                    if is_flagged:
                        add_flags.append('\\Flagged')
                    else:
                        remove_flags.append('\\Flagged')

                imap_client.set_flags(folder_name, uids, add_flags, remove_flags)
                return {'updated': uids, 'failed': []}
        except IMAPError as e:
            raise MailServiceError(f"Failed to set flags: {safe_exception_text(e)}")

    def _set_flags_jmap(
        self,
        account: 'MailAccount',
        email_ids: List[str],
        is_unread: Optional[bool],
        is_flagged: Optional[bool],
    ) -> Dict[str, Any]:
        """Set flags via JMAP."""
        password = ''
        if account.encrypted_incoming_password:
            password = self.credential_service.decrypt_password(
                account.encrypted_incoming_password
            )

        scheme = 'https' if account.incoming_security == 'ssl' else 'http'
        jmap_url = f"{scheme}://{account.incoming_host}:{account.incoming_port}"
        verify_ssl = getattr(settings, 'STALWART_VERIFY_SSL', True)

        with self._jmap_client(verify=verify_ssl) as client:
            session_response = client.get(
                f'{jmap_url}/jmap/session',
                auth=self._jmap_auth(account)
            )

            if session_response.status_code != 200:
                raise MailServiceError("Failed to get JMAP session: provider error body redacted")

            session = session_response.json()
            mail_account_id = session.get('primaryAccounts', {}).get('urn:ietf:params:jmap:mail')

            update = {}
            for email_id in email_ids:
                changes = {}
                if is_unread is not None:
                    changes['keywords/$seen'] = not is_unread
                if is_flagged is not None:
                    changes['keywords/$flagged'] = is_flagged
                if changes:
                    update[email_id] = changes

            if not update:
                return {'updated': [], 'failed': {}}

            response = client.post(
                f'{jmap_url}/jmap',
                auth=self._jmap_auth(account),
                headers={'Content-Type': 'application/json'},
                json={
                    'using': ['urn:ietf:params:jmap:core', 'urn:ietf:params:jmap:mail'],
                    'methodCalls': [
                        ['Email/set', {
                            'accountId': mail_account_id,
                            'update': update
                        }, 'flags']
                    ]
                }
            )

            if response.status_code != 200:
                raise MailServiceError("Failed to update flags: provider error body redacted")

            result = response.json()
            for method_response in result.get('methodResponses', []):
                if method_response[0] == 'Email/set':
                    data = method_response[1]
                    return {
                        'updated': list(data.get('updated', {}).keys()),
                        'failed': data.get('notUpdated', {}),
                    }

            return {'updated': [], 'failed': {}}


def get_mail_service(request=None) -> MailService:
    """Get a request-scoped service when a caller bearer is available."""
    if request is not None:
        cached = getattr(request, '_mail_request_service', None)
        if cached is not None:
            return cached
        authorization = request.META.get('HTTP_AUTHORIZATION', '')
        if authorization.lower().startswith('bearer '):
            delegated = getattr(request, '_mail74_provider_bearer', '')
            service = MailService(
                # Once preflight succeeds, retain only the purpose-bound
                # provider bearer on the service; the broader subject token is
                # no longer needed by the provider path.
                oauth_access_token='' if delegated else authorization[7:].strip(),
                delegated_mail_token=delegated,
            )
            request._mail_request_service = service
            return service
    return MailService()


def _on_success_update(sent_mailbox_id, drafts_mailbox_id):
    """MAIL-53: patch applied to the submitted email ONLY on successful
    EmailSubmission creation: clear $draft, move Drafts -> Sent."""
    update = {'keywords/$draft': None}
    if sent_mailbox_id:
        update[f'mailboxIds/{sent_mailbox_id}'] = True
    if drafts_mailbox_id:
        update[f'mailboxIds/{drafts_mailbox_id}'] = None
    return update
