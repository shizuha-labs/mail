"""
Views for Shizuha Mail API.

This is a generic email client that supports multiple accounts per user
from any IMAP/SMTP/JMAP provider.
"""
import base64
import json
import logging
import re
import uuid
from urllib.parse import quote
from datetime import timedelta
from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import validate_email
from django.utils import timezone
from asgiref.sync import async_to_sync
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView
from django.http import StreamingHttpResponse
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.exceptions import NotFound
from django.db import connection, IntegrityError, transaction
from django.db.models import F, Q, FloatField, Value
from django.db.models.functions import Cast
from django.contrib.postgres.search import SearchHeadline, SearchQuery, SearchRank

from .models import (
    MailAccount, MailFolder, MailThread, MailMessage,
    MailDraft, MailFilter, MailContact, OutboxMessage, MailUndoLog
)
from .folder_tree import (
    assert_custom_folder,
    reconcile_provider_folders,
    refresh_folder_counts_from_messages,
    server_folder_ref,
    serialize_folder_tree,
    sibling_name_exists,
)
from .redaction import public_provider_error, redact_sensitive, safe_exception_text
from .sync_utils import classify_connection_error
from .serializers import (
    MailAccountSerializer, MailAccountCreateSerializer, MailAccountUpdateSerializer,
    MailFolderSerializer, MailFolderCreateSerializer,
    MailThreadSerializer, MailMessageListSerializer, MailMessageDetailSerializer,
    MailMessageMoveSerializer, MailMessageFlagSerializer,
    MailDraftSerializer, MailSendSerializer,
    MailFilterSerializer, MailContactSerializer, MailSearchSerializer,
    EmailProviderPresetSerializer, ConnectionTestResultSerializer,
    InternalSendSerializer,
)

logger = logging.getLogger(__name__)

REPLY_REFERENCE_LIMIT = 50

# KOT-88: owner-scoped JMAP attachment blob proxy limits. The proxy streams the
# provider's blob through the API so mobile clients never need direct provider
# network access or provider credentials.
ATTACHMENT_PROXY_MAX_BYTES = 50 * 1024 * 1024  # 50 MiB streaming cap
_FILENAME_SANITIZE_RE = re.compile(r'[^A-Za-z0-9._ ()\[\]-]')
_SAFE_FILENAME_FALLBACK = 'attachment'


def _attachment_download_url(account, client, blob_id, filename):
    """Expand the provider's advertised JMAP downloadUrl for one blob.

    The session resource advertises a RFC 8620 `downloadUrl` template
    (`.../{accountId}/{blobId}/{name}`); opaque identifiers are percent-escaped
    before substitution so crafted ids cannot alter the URL path shape.
    Falls back to the canonical Stalwart template when the session does not
    advertise one.
    """
    session = async_to_sync(client.get_session)()
    template = (
        session.get('downloadUrl')
        or f'{client.base_url}/jmap/download/{{accountId}}/{{blobId}}/{{name}}'
    )
    escaped = {
        'accountId': quote(str(client.account_id or account.jmap_account_id or account.email_address), safe=''),
        'blobId': quote(blob_id, safe=''),
        'name': quote(filename, safe=''),
    }
    return template.format(accountId=escaped['accountId'], blobId=escaped['blobId'], name=escaped['name'])


class MailAttachmentDownloadView(APIView):
    """
    Owner-scoped JMAP attachment blob proxy (KOT-88).

    GET /mail/api/attachments/{blobId}/download/?account_id=…&filename=…

    Streams the provider blob to the authenticated owner of `account_id` so
    mobile clients never need provider network access or provider credentials.
    - Cross-owner / unknown / inactive account → non-enumerating 404
      (the account_not_found_response() shape).
    - IMAP accounts are refused explicitly (501): IMAP detail payloads carry no
      stable attachment identifier, so no download request is issued rather
      than a broken one.
    - Provider authentication stays server-side; the response applies
      `X-Content-Type-Options: nosniff` and a forced `attachment`
      Content-Disposition, and enforces a streaming 50 MiB cap.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, blob_id):
        blob_id = (blob_id or '').strip()
        if not blob_id:
            return account_not_found_response()

        account_id = request.query_params.get('account_id')
        account, error_response = resolve_account_for_request(request.user, account_id)
        if error_response:
            return error_response

        if account.incoming_server_type != 'jmap':
            return Response(
                {'error': 'Attachment download requires a JMAP account; '
                          'IMAP attachments are not downloadable yet.'},
                status=status.HTTP_501_NOT_IMPLEMENTED,
            )

        filename = request.query_params.get('filename') or _SAFE_FILENAME_FALLBACK
        safe_name = _FILENAME_SANITIZE_RE.sub('_', filename)
        safe_name = safe_name.replace('..', '_').lstrip('.')[:120] or _SAFE_FILENAME_FALLBACK

        try:
            from .services.credential_service import DecryptionError, get_credential_service
            cred = get_credential_service()
            password = cred.decrypt_password(account.encrypted_incoming_password)
        except DecryptionError:
            logger.error(
                'Attachment proxy: credential decrypt failed for account %s',
                redact_sensitive(account.id),
            )
            return Response(
                {'error': 'Attachment provider credentials are unavailable for this account.'},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        from .tasks import _get_stalwart_client
        client = _get_stalwart_client(account)
        # Keep the decrypted password exactly as the request-scoped client does.
        client.user_password = password

        try:
            upstream_url = _attachment_download_url(account, client, blob_id, safe_name)
        except Exception as e:
            logger.error(
                'Attachment proxy: session discovery failed for account %s: %s',
                redact_sensitive(account.id), safe_exception_text(e),
            )
            return Response(
                {'error': 'Attachment provider is unavailable.'},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        import httpx
        headers = client._get_headers()
        headers.pop('Content-Type', None)
        max_bytes = getattr(settings, 'MAIL_ATTACHMENT_PROXY_MAX_BYTES', ATTACHMENT_PROXY_MAX_BYTES)

        try:
            probe = httpx.head(upstream_url, headers=headers, timeout=30.0,
                               verify=client.verify_ssl, follow_redirects=True)
            if probe.status_code == 404:
                return Response({'error': 'Attachment not found'},
                                status=status.HTTP_404_NOT_FOUND)
            if probe.status_code >= 400:
                return Response({'error': 'Attachment provider refused the download.'},
                                status=status.HTTP_502_BAD_GATEWAY)
            declared = probe.headers.get('content-length')
            if declared and declared.isdigit() and int(declared) > max_bytes:
                return Response(
                    {'error': f'Attachment exceeds the {max_bytes // (1024 * 1024)} MiB download cap.'},
                    status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                )
            content_type = probe.headers.get('content-type') or 'application/octet-stream'

            def _stream():
                sent = 0
                with httpx.stream('GET', upstream_url, headers=headers, timeout=30.0,
                                  verify=client.verify_ssl, follow_redirects=True) as upstream:
                    upstream.raise_for_status()
                    for chunk in upstream.iter_bytes(chunk_size=64 * 1024):
                        sent += len(chunk)
                        if sent > max_bytes:
                            logger.warning(
                                'Attachment proxy: stream cap exceeded mid-flight for account %s',
                                redact_sensitive(account.id),
                            )
                            break
                        yield chunk

            response = StreamingHttpResponse(_stream(), content_type=content_type)
        except httpx.HTTPError as e:
            logger.error(
                'Attachment proxy: provider fetch failed for account %s: %s',
                redact_sensitive(account.id), safe_exception_text(e),
            )
            return Response({'error': 'Attachment provider is unavailable.'},
                            status=status.HTTP_502_BAD_GATEWAY)

        response['X-Content-Type-Options'] = 'nosniff'
        response['Content-Disposition'] = f"attachment; filename*=UTF-8''{quote(safe_name, safe='')}"
        return response


def _normalise_reply_message_ids(message):
    """Derive an ordered RFC thread chain, capped at recent provider context."""
    parent_id = str(message.get('message_id') or '').strip()
    references = []
    for message_id in (message.get('references') or []):
        message_id = str(message_id or '').strip()
        if message_id and message_id not in references:
            references.append(message_id)
    inherited_parent = str(message.get('in_reply_to') or '').strip()
    if inherited_parent and inherited_parent not in references:
        references.append(inherited_parent)
    if parent_id and parent_id not in references:
        references.append(parent_id)
    return parent_id, references[-REPLY_REFERENCE_LIMIT:]


# Email provider presets for common providers
EMAIL_PROVIDER_PRESETS = [
    {
        'id': 'gmail',
        'name': 'Gmail',
        'domains': ['gmail.com', 'googlemail.com'],
        'incoming_host': 'imap.gmail.com',
        'incoming_port': 993,
        'incoming_security': 'ssl',
        'incoming_server_type': 'imap',
        'outgoing_host': 'smtp.gmail.com',
        'outgoing_port': 587,
        'outgoing_security': 'starttls',
        'notes': 'Requires App Password if 2FA is enabled',
    },
    {
        'id': 'outlook',
        'name': 'Outlook / Microsoft 365',
        'domains': ['outlook.com', 'hotmail.com', 'live.com', 'msn.com'],
        'incoming_host': 'outlook.office365.com',
        'incoming_port': 993,
        'incoming_security': 'ssl',
        'incoming_server_type': 'imap',
        'outgoing_host': 'smtp.office365.com',
        'outgoing_port': 587,
        'outgoing_security': 'starttls',
        'notes': '',
    },
    {
        'id': 'yahoo',
        'name': 'Yahoo Mail',
        'domains': ['yahoo.com', 'ymail.com'],
        'incoming_host': 'imap.mail.yahoo.com',
        'incoming_port': 993,
        'incoming_security': 'ssl',
        'incoming_server_type': 'imap',
        'outgoing_host': 'smtp.mail.yahoo.com',
        'outgoing_port': 465,
        'outgoing_security': 'ssl',
        'notes': 'Requires App Password',
    },
    {
        'id': 'icloud',
        'name': 'iCloud Mail',
        'domains': ['icloud.com', 'me.com', 'mac.com'],
        'incoming_host': 'imap.mail.me.com',
        'incoming_port': 993,
        'incoming_security': 'ssl',
        'incoming_server_type': 'imap',
        'outgoing_host': 'smtp.mail.me.com',
        'outgoing_port': 587,
        'outgoing_security': 'starttls',
        'notes': 'Requires App-Specific Password',
    },
    {
        'id': 'stalwart',
        'name': 'Shizuha Mail (Stalwart)',
        'domains': ['shizuha.com', 'shizuha.local'],
        'incoming_host': 'mail-shadow.shizuha.com',
        'incoming_port': 443,
        'incoming_security': 'ssl',
        'incoming_server_type': 'jmap',
        'outgoing_host': 'mail-shadow.shizuha.com',
        'outgoing_port': 587,
        'outgoing_security': 'starttls',
        'notes': 'Shizuha ID managed Stalwart mail server',
    },
    {
        'id': 'custom',
        'name': 'Custom / Other',
        'domains': [],
        'incoming_host': '',
        'incoming_port': 993,
        'incoming_security': 'ssl',
        'incoming_server_type': 'imap',
        'outgoing_host': '',
        'outgoing_port': 587,
        'outgoing_security': 'starttls',
        'notes': '',
    },
]


def get_user_accounts(user):
    """Get all mail accounts for a user."""
    return MailAccount.objects.filter(user_id=user.id, is_active=True)


def get_default_account(user):
    """Get the default mail account for a user."""
    return MailAccount.objects.filter(
        user_id=user.id,
        is_active=True,
        is_default=True
    ).first() or MailAccount.objects.filter(
        user_id=user.id,
        is_active=True
    ).first()


def get_account_by_id(user, account_id):
    """Get a specific active account by ID, ensuring it belongs to the caller."""
    return MailAccount.objects.filter(
        id=account_id,
        user_id=user.id,
        is_active=True
    ).first()


def account_not_found_response():
    """Non-enumerating 404 for nonexistent or cross-owner account IDs."""
    return Response({'error': 'Mail account not found'}, status=status.HTTP_404_NOT_FOUND)


def resolve_account_for_request(user, account_id=None):
    """Resolve account from server-side principal; never trust client owner fields."""
    if account_id:
        account = get_account_by_id(user, account_id)
        if not account:
            return None, account_not_found_response()
        return account, None
    account = get_default_account(user)
    if not account:
        return None, Response({'error': 'No accounts configured'}, status=status.HTTP_404_NOT_FOUND)
    return account, None


def _enforce_send_quota(account):
    """Return 429 when an account has exhausted either rolling send window."""
    limits = getattr(settings, 'MAIL_SEND_QUOTA', {'per_minute': 5, 'per_day': 50})
    now = timezone.now()
    windows = (
        ('per_minute', now - timedelta(minutes=1), 'Send rate limit', 'minute'),
        ('per_day', now - timedelta(days=1), 'Send quota', 'day'),
    )
    for key, cutoff, label, period in windows:
        limit = limits[key]
        used = OutboxMessage.objects.filter(
            account=account,
            created_at__gte=cutoff,
        ).count()
        if used >= limit:
            return Response(
                {
                    'status': 'error',
                    'error': f'{label} exceeded: at most {limit} sends per {period}',
                },
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )
    return None



def _preflight_managed_identity_exchange(request, account):
    """Fail closed before outbox create when managed_identity exchange cannot mint.

    Returns a Response on failure, else None. Caches bearer on the request so the
    subsequent MailService path does not double-exchange when possible.
    """
    if not (getattr(account, 'settings', None) or {}).get('managed_identity'):
        return None
    from .services.delegated_token import (
        DelegatedTokenError,
        DelegatedTokenExpired,
        resolve_provider_bearer,
    )

    auth = request.META.get('HTTP_AUTHORIZATION', '')
    subject = auth.split(' ', 1)[1].strip() if auth.lower().startswith('bearer ') else ''
    try:
        bearer = resolve_provider_bearer(
            subject_access_token=subject,
            managed_identity=True,
        )
    except DelegatedTokenExpired as exc:
        # MAIL-131: the CALLER's own access token is expired/revoked — ID
        # rejected it at token-exchange (invalid_grant -> HTTP 401). The
        # mailbox is healthy; surface a clear 401 so the caller re-authenticates
        # instead of looping on a cryptic 503 and reading the mailbox as broken.
        return Response(
            {
                'status': 'error',
                'error': safe_exception_text(exc),
                'code': 'caller_token_expired',
            },
            status=status.HTTP_401_UNAUTHORIZED,
        )
    except DelegatedTokenError as exc:
        return Response(
            {
                'status': 'error',
                'error': safe_exception_text(exc),
            },
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    # Request-scoped cache for this send only (never persisted).
    request._mail74_provider_bearer = bearer
    return None


def _consume_failed_preprovider_outbox(existing):
    """Re-arm only a typed failure proven to precede all provider I/O.

    Error strings are untrusted provider text and can never establish retry
    safety. ``False`` is written by our send path; ``None`` is deliberately
    unknown so legacy rows remain fail-closed.
    """
    if (
        existing is not None
        and existing.status == 'failed'
        and existing.provider_started is False
    ):
        existing.delete()
        return True
    return False

def generic_mail_error_response(message='Mail provider request failed', status_code=status.HTTP_502_BAD_GATEWAY):
    """Public error payload for provider failures; details belong only in redacted logs."""
    return Response({'error': public_provider_error(message)}, status=status_code)


class MailAccountViewSet(viewsets.ModelViewSet):
    """
    Mail account management.
    Users can have multiple email accounts from any provider.
    """
    serializer_class = MailAccountSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return MailAccount.objects.filter(user_id=self.request.user.id)

    def get_serializer_class(self):
        if self.action == 'create':
            return MailAccountCreateSerializer
        if self.action in ['update', 'partial_update', 'update_preferences']:
            return MailAccountUpdateSerializer
        return MailAccountSerializer

    def list(self, request):
        """List all mail accounts for the current user."""
        accounts = self.get_queryset()
        serializer = MailAccountSerializer(accounts, many=True)
        return Response(serializer.data)

    def create(self, request):
        """Add a new email account."""
        from .services.mail_service import get_mail_service
        from .services.credential_service import get_credential_service

        serializer = MailAccountCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        data = serializer.validated_data

        # Encrypt passwords
        credential_service = get_credential_service()
        encrypted_incoming = credential_service.encrypt_password(data['incoming_password'])

        encrypted_outgoing = None
        if data.get('outgoing_password'):
            encrypted_outgoing = credential_service.encrypt_password(data['outgoing_password'])
        elif data.get('outgoing_requires_auth', True):
            # Use incoming password for outgoing if not specified
            encrypted_outgoing = encrypted_incoming

        # Check if account already exists for this user - update it instead of rejecting
        existing = MailAccount.objects.filter(
            user_id=request.user.id,
            email_address=data['email_address']
        ).first()

        if existing:
            existing.display_name = data.get('display_name', '') or existing.display_name
            existing.from_name = data.get('from_name', '') or existing.from_name
            existing.incoming_server_type = data.get('incoming_server_type', 'imap')
            existing.incoming_host = data['incoming_host']
            existing.incoming_port = data.get('incoming_port', 993)
            existing.incoming_security = data.get('incoming_security', 'ssl')
            existing.incoming_username = data['incoming_username']
            existing.encrypted_incoming_password = encrypted_incoming
            existing.outgoing_host = data['outgoing_host']
            existing.outgoing_port = data.get('outgoing_port', 587)
            existing.outgoing_security = data.get('outgoing_security', 'starttls')
            existing.outgoing_username = data.get('outgoing_username', '') or data['incoming_username']
            existing.encrypted_outgoing_password = encrypted_outgoing
            existing.outgoing_requires_auth = data.get('outgoing_requires_auth', True)
            existing.is_active = True
            existing.save()

            return Response(MailAccountSerializer(existing).data, status=status.HTTP_200_OK)

        # Determine if this should be the default account
        is_default = data.get('is_default', False)
        if not MailAccount.objects.filter(user_id=request.user.id, is_active=True).exists():
            is_default = True  # First account is always default

        # Create the account
        account = MailAccount.objects.create(
            user_id=request.user.id,
            display_name=data.get('display_name', ''),
            from_name=data.get('from_name', ''),
            email_address=data['email_address'],
            incoming_server_type=data.get('incoming_server_type', 'imap'),
            incoming_host=data['incoming_host'],
            incoming_port=data.get('incoming_port', 993),
            incoming_security=data.get('incoming_security', 'ssl'),
            incoming_username=data['incoming_username'],
            encrypted_incoming_password=encrypted_incoming,
            outgoing_host=data['outgoing_host'],
            outgoing_port=data.get('outgoing_port', 587),
            outgoing_security=data.get('outgoing_security', 'starttls'),
            outgoing_username=data.get('outgoing_username', '') or data['incoming_username'],
            encrypted_outgoing_password=encrypted_outgoing,
            outgoing_requires_auth=data.get('outgoing_requires_auth', True),
            signature=data.get('signature', ''),
            is_default=is_default,
        )

        return Response(
            MailAccountSerializer(account).data,
            status=status.HTTP_201_CREATED
        )

    def destroy(self, request, pk=None):
        """Delete an email account."""
        account = self.get_object()

        # If deleting default account, set another as default
        if account.is_default:
            other_account = MailAccount.objects.filter(
                user_id=request.user.id,
                is_active=True
            ).exclude(pk=account.pk).first()
            if other_account:
                other_account.is_default = True
                other_account.save()

        # Soft delete by marking inactive, or hard delete
        account.delete()

        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=False, methods=['get'])
    def me(self, request):
        """Get the default mail account (for backwards compatibility)."""
        account = get_default_account(request.user)
        if not account:
            return Response(
                {'account': None, 'message': 'No mail accounts configured. Please add an account first.'},
                status=status.HTTP_200_OK
            )
        serializer = MailAccountSerializer(account)
        return Response(serializer.data)

    @action(detail=True, methods=['post'])
    def test(self, request, pk=None):
        """Test connection to an existing email account."""
        from .services.mail_service import get_mail_service

        account = self.get_object()
        mail_service = get_mail_service(request)

        try:
            results = mail_service.test_connection(account)

            # Update connection status
            if results['incoming']['success'] and results['outgoing']['success']:
                account.connection_status = 'connected'
                account.connection_error = ''
            elif not results['incoming']['success']:
                # MAIL-24: shared classifier (no fork) — same auth→ssl→else substring logic.
                # MAIL-31: pass auth_type so an OAuth account whose token expired during a
                # manual "Test Connection" gets needs_reauth (not auth_failed), matching the
                # sync-task path (MAIL-28).
                account.connection_status = classify_connection_error(
                    message=results['incoming'].get('message', ''),
                    auth_type=account.auth_type,
                )
                account.connection_error = redact_sensitive(results['incoming'].get('message', ''))
            else:
                account.connection_status = 'connection_failed'
                account.connection_error = redact_sensitive(results['outgoing'].get('message', ''))

            account.last_connection_check = timezone.now()
            account.save()

            return Response(redact_sensitive(results))

        except Exception as e:
            logger.error("Connection test failed: %s", safe_exception_text(e))
            return generic_mail_error_response('Connection test failed')

    @action(detail=False, methods=['post'], url_path='test-connection')
    def test_connection(self, request):
        """
        Test connection with provided credentials without creating an account.
        Used by the Add Account form to verify settings before saving.
        """
        from .services.mail_service import get_mail_service
        from .services.credential_service import get_credential_service

        data = request.data
        required = ['incoming_host', 'incoming_username', 'incoming_password', 'outgoing_host']
        missing = [f for f in required if not data.get(f)]
        if missing:
            return Response({'error': f'Missing fields: {", ".join(missing)}'}, status=400)

        # Build a temporary (unsaved) MailAccount for testing
        credential_service = get_credential_service()
        encrypted_password = credential_service.encrypt_password(data['incoming_password'])

        account = MailAccount(
            user_id=request.user.id,
            email_address=data.get('email_address', ''),
            incoming_server_type=data.get('incoming_server_type', 'imap'),
            incoming_host=data['incoming_host'],
            incoming_port=int(data.get('incoming_port', 993)),
            incoming_security=data.get('incoming_security', 'ssl'),
            incoming_username=data['incoming_username'],
            encrypted_incoming_password=encrypted_password,
            outgoing_host=data['outgoing_host'],
            outgoing_port=int(data.get('outgoing_port', 587)),
            outgoing_security=data.get('outgoing_security', 'starttls'),
            outgoing_username=data.get('outgoing_username', '') or data['incoming_username'],
            encrypted_outgoing_password=encrypted_password,
            outgoing_requires_auth=True,
        )

        mail_service = get_mail_service(request)

        try:
            results = mail_service.test_connection(account)
            return Response(redact_sensitive(results))
        except Exception as e:
            logger.error("Connection test failed: %s", safe_exception_text(e))
            return generic_mail_error_response('Connection test failed')

    @action(detail=True, methods=['post'], url_path='set-default')
    def set_default(self, request, pk=None):
        """Set this account as the default."""
        account = self.get_object()
        account.is_default = True
        account.save()  # The save() method handles unsetting other defaults
        return Response(MailAccountSerializer(account).data)

    @action(detail=False, methods=['patch'], url_path='preferences', url_name='preferences')
    def update_preferences(self, request):
        """Update account preferences for the caller's default or requested account."""
        account_id = request.data.get('account_id') or request.query_params.get('account_id')
        account, error_response = resolve_account_for_request(request.user, account_id)
        if error_response:
            return error_response

        serializer = MailAccountUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        if 'settings' in data:
            account.settings = data['settings']
        if 'signature' in data:
            account.signature = data['signature']
        if 'display_name' in data:
            account.display_name = data['display_name']
        if 'from_name' in data:
            account.from_name = data['from_name']
        if 'is_default' in data:
            account.is_default = data['is_default']

        # Handle password updates
        if 'incoming_password' in data:
            from .services.credential_service import get_credential_service
            credential_service = get_credential_service()
            account.encrypted_incoming_password = credential_service.encrypt_password(data['incoming_password'])

        if 'outgoing_password' in data and data['outgoing_password']:
            from .services.credential_service import get_credential_service
            credential_service = get_credential_service()
            account.encrypted_outgoing_password = credential_service.encrypt_password(data['outgoing_password'])

        account.save()
        return Response(MailAccountSerializer(account).data)

    @action(detail=False, methods=['post'])
    def sync(self, request):
        """Trigger a sync with mail server (for default account)."""
        account_id = request.data.get('account_id')
        account, error_response = resolve_account_for_request(request.user, account_id)
        if error_response:
            return error_response

        if (account.settings or {}).get('managed_identity'):
            # Managed mail has no durable provider credential by design. Mint a
            # purpose-bound bearer for this request and perform the provider
            # work inline; never put either bearer on a Celery queue.
            preflight = _preflight_managed_identity_exchange(request, account)
            if preflight is not None:
                return preflight

            from .services.stalwart_client import StalwartJMAPClient
            from .tasks import _sync_jmap_messages

            client = StalwartJMAPClient(
                user_email=account.email_address,
                auth_token=request._mail74_provider_bearer,
                base_url=settings.UNIFIED_MAIL_JMAP_URL,
            )
            try:
                # Resolve the caller's provider account ID before issuing
                # Mailbox/get or Email/query with this fresh client.
                async_to_sync(client.get_session)()
                folders = async_to_sync(client.get_mailboxes)()
                reconcile_provider_folders(account, folders)
                new_messages = _sync_jmap_messages(account, client=client)
            except Exception as exc:
                logger.error(
                    'Request-scoped managed sync failed for account %s: %s',
                    redact_sensitive(account.id),
                    safe_exception_text(exc),
                )
                return generic_mail_error_response(
                    'Mail sync failed',
                    status.HTTP_502_BAD_GATEWAY,
                )

            account.connection_status = 'connected'
            account.connection_error = ''
            account.last_sync_at = timezone.now()
            account.save(update_fields=[
                'connection_status',
                'connection_error',
                'last_sync_at',
                'updated_at',
            ])
            return Response({
                'status': 'synced',
                'account_id': str(account.id),
                'synced_accounts': 1,
                'new_messages': new_messages,
            })

        # Dispatch sync tasks
        from .tasks import sync_mailbox_counts, sync_new_messages
        sync_mailbox_counts.delay(str(account.id), expected_user_id=request.user.id)
        sync_new_messages.delay(str(account.id), expected_user_id=request.user.id)
        return Response({'status': 'sync_queued', 'account_id': str(account.id)})


class EmailProviderViewSet(viewsets.ViewSet):
    """
    Email provider presets endpoint.
    """
    permission_classes = [IsAuthenticated]

    def list(self, request):
        """Get list of email provider presets."""
        serializer = EmailProviderPresetSerializer(EMAIL_PROVIDER_PRESETS, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=['get'])
    def detect(self, request):
        """Detect provider from email domain."""
        email = request.query_params.get('email', '')
        if '@' not in email:
            return Response({'error': 'Invalid email'}, status=400)

        domain = email.split('@')[1].lower()

        for preset in EMAIL_PROVIDER_PRESETS:
            if domain in preset.get('domains', []):
                return Response(preset)

        # Return custom preset if no match
        return Response(EMAIL_PROVIDER_PRESETS[-1])  # 'custom'


class MailFolderViewSet(viewsets.ModelViewSet):
    """
    Mail folder (mailbox) management.
    Fetches folders from the mail server.
    """
    serializer_class = MailFolderSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        account_id = self.request.query_params.get('account_id')
        if account_id:
            account = get_account_by_id(self.request.user, account_id)
        else:
            account = get_default_account(self.request.user)

        if not account:
            return MailFolder.objects.none()

        # Detail actions (rename/delete/retrieve) must be able to resolve child
        # folders too; list/tree apply their own root/tree shaping below.
        return MailFolder.objects.filter(account=account)

    def get_serializer_class(self):
        if self.action == 'create':
            return MailFolderCreateSerializer
        return MailFolderSerializer

    def list(self, request):
        """Get all folders from mail server."""
        from .services.mail_service import get_mail_service, MailServiceError

        account_id = request.query_params.get('account_id')
        account, error_response = resolve_account_for_request(request.user, account_id)
        if error_response:
            return error_response

        mail_service = get_mail_service(request)

        try:
            folders = mail_service.sync_folders(account)
            reconcile_provider_folders(account, folders)
            return Response(folders)

        except MailServiceError as e:
            logger.error("Failed to fetch folders: %s", safe_exception_text(e))
            # Return cached folders if available, otherwise return default folder structure
            cached_folders = list(MailFolder.objects.filter(account=account).values(
                'id', 'name', 'role', 'total_messages', 'unread_messages', 'jmap_mailbox_id'
            ))
            if cached_folders:
                return Response(cached_folders)
            # Return default folder structure so the UI isn't empty
            return Response([
                {'name': 'Inbox', 'role': 'inbox', 'total_messages': 0, 'unread_messages': 0},
                {'name': 'Sent', 'role': 'sent', 'total_messages': 0, 'unread_messages': 0},
                {'name': 'Drafts', 'role': 'drafts', 'total_messages': 0, 'unread_messages': 0},
                {'name': 'Trash', 'role': 'trash', 'total_messages': 0, 'unread_messages': 0},
                {'name': 'Junk', 'role': 'junk', 'total_messages': 0, 'unread_messages': 0},
            ])
        except Exception as e:
            logger.error("Unexpected error fetching folders: %s", safe_exception_text(e))
            return Response([
                {'name': 'Inbox', 'role': 'inbox', 'total_messages': 0, 'unread_messages': 0},
                {'name': 'Sent', 'role': 'sent', 'total_messages': 0, 'unread_messages': 0},
                {'name': 'Drafts', 'role': 'drafts', 'total_messages': 0, 'unread_messages': 0},
                {'name': 'Trash', 'role': 'trash', 'total_messages': 0, 'unread_messages': 0},
            ])

    def _resolve_parent(self, account, parent_id):
        if not parent_id:
            return None
        try:
            return MailFolder.objects.get(id=parent_id, account=account)
        except MailFolder.DoesNotExist:
            return None

    def _create_provider_folder(self, account, name, parent):
        if account.incoming_server_type != 'jmap':
            return ''
        from .tasks import _get_stalwart_client

        client = _get_stalwart_client(account)
        if client is None:
            return ''
        created = async_to_sync(client.create_mailbox)(
            name,
            parent_id=parent.jmap_mailbox_id if parent and parent.jmap_mailbox_id else None,
        )
        return created.get('id') or ''

    def _update_provider_folder(self, account, folder, name=None, parent=None, parent_supplied=False):
        if account.incoming_server_type != 'jmap' or not folder.jmap_mailbox_id:
            return
        from .services.mail_service import get_mail_service

        update = {}
        if name is not None:
            update['name'] = name
        if parent_supplied:
            update['parentId'] = parent.jmap_mailbox_id or None
        if not update:
            return
        get_mail_service(request).update_folder(account, folder.jmap_mailbox_id, update)

    def _delete_provider_folder(self, account, folder):
        if account.incoming_server_type != 'jmap' or not folder.jmap_mailbox_id:
            return
        from .services.mail_service import get_mail_service

        get_mail_service(request).delete_folder(account, folder.jmap_mailbox_id)

    def create(self, request):
        """Create a new mail folder."""
        serializer = MailFolderCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        account_id = request.data.get('account_id')
        account, error_response = resolve_account_for_request(request.user, account_id)
        if error_response:
            return error_response

        name = serializer.validated_data['name'].strip()
        if not name:
            return Response({'error': 'Folder name is required'}, status=400)

        parent = None
        if serializer.validated_data.get('parent_id'):
            parent = self._resolve_parent(account, serializer.validated_data['parent_id'])
            if parent is None:
                return Response({'error': 'Parent folder not found'}, status=404)

        if sibling_name_exists(account, name, parent):
            return Response({'error': 'A folder with this name already exists at this level'}, status=409)

        with transaction.atomic():
            folder = MailFolder.objects.create(
                account=account,
                name=name,
                parent=parent,
                role='custom',
                # IMAP has no provider CRUD implementation in this service yet;
                # use a deterministic account-local server ref for local cache.
                jmap_mailbox_id=f'imap:{name}' if account.incoming_server_type == 'imap' else '',
            )

            # MAIL-55/MAIL-61: actually create the folder on the mail server.
            try:
                provider_id = self._create_provider_folder(account, name, parent)
                if provider_id:
                    folder.jmap_mailbox_id = provider_id
                    folder.save(update_fields=['jmap_mailbox_id', 'updated_at'])
            except Exception as e:
                # Local row is kept so the UI folder isn't lost; a later filter
                # application self-heals the missing mailbox (filter_engine).
                logger.warning(
                    "MAIL-55: failed to create Stalwart mailbox for folder %s: %s",
                    redact_sensitive(str(folder.id)), safe_exception_text(e),
                )

        return Response(
            MailFolderSerializer(folder).data,
            status=status.HTTP_201_CREATED
        )

    def partial_update(self, request, pk=None):
        return self.update(request, pk=pk, partial=True)

    def update(self, request, pk=None, partial=False):
        """Rename or re-parent a custom folder with special-folder guardrails."""
        account_id = request.data.get('account_id') or request.query_params.get('account_id')
        account, error_response = resolve_account_for_request(request.user, account_id)
        if error_response:
            return error_response

        folder = MailFolder.objects.filter(account=account, pk=pk).first()
        if folder is None:
            return Response({'error': 'Folder not found'}, status=404)

        try:
            assert_custom_folder(folder, 'rename or move')
        except ValueError as e:
            return Response({'error': str(e)}, status=409)

        name = request.data.get('name', folder.name)
        name = name.strip() if isinstance(name, str) else folder.name
        if not name:
            return Response({'error': 'Folder name is required'}, status=400)

        parent = folder.parent
        if 'parent_id' in request.data:
            parent_id = request.data.get('parent_id')
            parent = self._resolve_parent(account, parent_id) if parent_id else None
            if parent_id and parent is None:
                return Response({'error': 'Parent folder not found'}, status=404)
            if parent and parent.id == folder.id:
                return Response({'error': 'Folder cannot be its own parent'}, status=400)

        if sibling_name_exists(account, name, parent, exclude=folder):
            return Response({'error': 'A folder with this name already exists at this level'}, status=409)

        try:
            with transaction.atomic():
                self._update_provider_folder(
                    account,
                    folder,
                    name=name,
                    parent=parent,
                    parent_supplied='parent_id' in request.data,
                )
                folder.name = name
                folder.parent = parent
                folder.save(update_fields=['name', 'parent', 'updated_at'])
        except Exception as e:
            logger.error("Failed to update folder: %s", safe_exception_text(e))
            return generic_mail_error_response('Could not update folder')

        return Response(MailFolderSerializer(folder).data)

    def destroy(self, request, pk=None):
        """Delete only empty custom folders; special folders are immutable."""
        account_id = request.data.get('account_id') or request.query_params.get('account_id')
        account, error_response = resolve_account_for_request(request.user, account_id)
        if error_response:
            return error_response

        folder = MailFolder.objects.filter(account=account, pk=pk).first()
        if folder is None:
            return Response({'error': 'Folder not found'}, status=404)

        try:
            assert_custom_folder(folder, 'delete')
        except ValueError as e:
            return Response({'error': str(e)}, status=409)
        if folder.children.exists():
            return Response({'error': 'Cannot delete a folder that has child folders'}, status=409)
        if folder.messages.exists():
            return Response({'error': 'Cannot delete a folder that contains messages'}, status=409)

        try:
            with transaction.atomic():
                self._delete_provider_folder(account, folder)
                folder.delete()
        except Exception as e:
            logger.error("Failed to delete folder: %s", safe_exception_text(e))
            return generic_mail_error_response('Could not delete folder')

        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=False, methods=['get'])
    def tree(self, request):
        """Get an account-scoped, stable folder tree structure."""
        from .services.mail_service import get_mail_service, MailServiceError

        account_id = request.query_params.get('account_id')
        account, error_response = resolve_account_for_request(request.user, account_id)
        if error_response:
            return error_response

        try:
            folders = get_mail_service(request).sync_folders(account)
            reconcile_provider_folders(account, folders)
        except MailServiceError as e:
            logger.warning("Folder tree using cached folders after sync failure: %s", safe_exception_text(e))

        return Response({
            'account_id': str(account.id),
            'results': serialize_folder_tree(account),
        })

    @action(detail=False, methods=['get'], url_path='get_folder_tree')
    def get_folder_tree(self, request):
        """Backward-compatible alias used by MCP/frontend callers."""
        return self.tree(request)


class MailLabelViewSet(viewsets.ViewSet):
    """Account-scoped message label definitions backed by account settings.

    Mail labels are JMAP/keyword-style strings stored on MailMessage.labels.
    Empty label definitions live in MailAccount.settings['labels'] so users can
    create/rename/delete labels before any message is tagged.
    """
    permission_classes = [IsAuthenticated]

    def _account(self, request):
        account_id = request.data.get('account_id') if hasattr(request, 'data') else None
        account_id = account_id or request.query_params.get('account_id')
        return resolve_account_for_request(request.user, account_id)

    def _used_labels(self, account):
        """Return labels currently present on messages without materializing rows.

        Production uses PostgreSQL JSONB expansion so the database returns one
        DISTINCT row per label. SQLite/no-migrations tests do not support that
        function, so they fall back to a chunked cursor over only the JSON
        column.
        """
        used = set()
        if connection.vendor == 'postgresql':
            table = connection.ops.quote_name(MailMessage._meta.db_table)
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT DISTINCT label.value
                    FROM {table}
                    CROSS JOIN LATERAL jsonb_array_elements_text(
                        CASE
                            WHEN jsonb_typeof(labels) = 'array' THEN labels
                            ELSE '[]'::jsonb
                        END
                    ) AS label(value)
                    WHERE account_id = %s
                    """,
                    [account.id],
                )
                used.update(str(row[0]) for row in cursor.fetchall() if str(row[0]).strip())
            return used

        rows = (
            MailMessage.objects
            .filter(account=account)
            .exclude(labels=[])
            .values_list('labels', flat=True)
            .iterator(chunk_size=500)
        )
        for labels in rows:
            if isinstance(labels, list):
                used.update(str(label) for label in labels if str(label).strip())
        return used

    def _labels(self, account):
        configured = account.settings.get('labels', []) if isinstance(account.settings, dict) else []
        configured_labels = {str(label).strip() for label in configured if str(label).strip()}
        return sorted(configured_labels | self._used_labels(account), key=str.lower)

    def _message_queryset_for_label(self, account, label):
        qs = MailMessage.objects.filter(account=account).only('id', 'labels', 'updated_at')
        if connection.vendor == 'postgresql':
            qs = qs.filter(labels__contains=[label])
        return qs

    def _rewrite_message_label(self, account, old_label, new_label=None, *, chunk_size=100):
        """Rename or remove a label on matching messages with bounded memory."""
        batch = []
        changed = 0

        def flush():
            nonlocal batch
            if not batch:
                return
            MailMessage.objects.bulk_update(batch, ['labels', 'updated_at'], batch_size=chunk_size)
            batch = []

        for message in self._message_queryset_for_label(account, old_label).iterator(chunk_size=chunk_size):
            labels = message.labels or []
            if old_label not in labels:
                continue
            if new_label is None:
                rewritten = [label for label in labels if label != old_label]
            else:
                rewritten = [new_label if label == old_label else label for label in labels]
            if rewritten == labels:
                continue
            message.labels = rewritten
            message.updated_at = timezone.now()
            batch.append(message)
            changed += 1
            if len(batch) >= chunk_size:
                flush()
        flush()
        return changed

    def _save_labels(self, account, labels):
        settings_data = dict(account.settings or {})
        settings_data['labels'] = sorted({label.strip() for label in labels if label and label.strip()}, key=str.lower)
        account.settings = settings_data
        account.save(update_fields=['settings', 'updated_at'])

    def list(self, request):
        account, error_response = self._account(request)
        if error_response:
            return error_response
        return Response({'account_id': str(account.id), 'results': [{'name': label} for label in self._labels(account)]})

    def create(self, request):
        account, error_response = self._account(request)
        if error_response:
            return error_response
        name = str(request.data.get('name', '')).strip()
        if not name:
            return Response({'error': 'Label name is required'}, status=400)
        labels = self._labels(account)
        if name.lower() in {label.lower() for label in labels}:
            return Response({'error': 'Label already exists'}, status=409)
        labels.append(name)
        self._save_labels(account, labels)
        return Response({'name': name}, status=status.HTTP_201_CREATED)

    def partial_update(self, request, pk=None):
        return self.update(request, pk=pk)

    def update(self, request, pk=None):
        account, error_response = self._account(request)
        if error_response:
            return error_response
        old_name = str(pk or '').strip()
        new_name = str(request.data.get('name', '')).strip()
        if not old_name or not new_name:
            return Response({'error': 'Both old and new label names are required'}, status=400)

        labels = self._labels(account)
        label_by_lower = {label.lower(): label for label in labels}
        if old_name.lower() not in label_by_lower:
            return Response({'error': 'Label not found'}, status=404)
        if new_name.lower() != old_name.lower() and new_name.lower() in label_by_lower:
            return Response({'error': 'Label already exists'}, status=409)

        canonical_old = label_by_lower[old_name.lower()]
        with transaction.atomic():
            self._rewrite_message_label(account, canonical_old, new_name)
            self._save_labels(account, [new_name if label == canonical_old else label for label in labels])
        return Response({'name': new_name})

    def destroy(self, request, pk=None):
        account, error_response = self._account(request)
        if error_response:
            return error_response
        label_name = str(pk or '').strip()
        labels = self._labels(account)
        label_by_lower = {label.lower(): label for label in labels}
        if label_name.lower() not in label_by_lower:
            return Response(status=status.HTTP_204_NO_CONTENT)
        canonical = label_by_lower[label_name.lower()]
        with transaction.atomic():
            self._rewrite_message_label(account, canonical, None)
            self._save_labels(account, [label for label in labels if label != canonical])
        return Response(status=status.HTTP_204_NO_CONTENT)


def _undo_window_seconds():
    """Bounded undo window (default 60s, configurable via settings)."""
    return int(getattr(settings, 'MAIL_UNDO_WINDOW_SECONDS', 60))


def _is_uuid(value):
    try:
        uuid.UUID(str(value))
        return True
    except (TypeError, ValueError):
        return False


def _record_undo_log(request, account, op_type, message_ids, prior_state, target_folder_id=None):
    """MAIL-162: record a reversible bulk op for bounded undo.

    ``prior_state`` maps message-id string -> {'folder_id': str|None,
    'is_flagged': bool, 'is_unread': bool}. Permanent delete never records.
    """
    if not message_ids:
        return None
    return MailUndoLog.objects.create(
        account=account,
        user_id=request.user.id,
        op_type=op_type,
        message_ids=list(message_ids),
        prior_state=prior_state,
        target_folder_id=target_folder_id,
        expires_at=timezone.now() + timedelta(seconds=_undo_window_seconds()),
    )


class MailMessageViewSet(viewsets.ModelViewSet):
    """
    Mail message management.
    Fetches messages from mail server.
    """
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        account_id = self.request.query_params.get('account_id')
        if account_id:
            account = get_account_by_id(self.request.user, account_id)
        else:
            account = get_default_account(self.request.user)

        if not account:
            return MailMessage.objects.none()

        queryset = MailMessage.objects.filter(account=account)

        folder_id = self.request.query_params.get('folder')
        if folder_id:
            queryset = queryset.filter(folder_id=folder_id)

        is_unread = self.request.query_params.get('is_unread')
        if is_unread is not None:
            queryset = queryset.filter(is_unread=is_unread.lower() == 'true')

        is_flagged = self.request.query_params.get('is_flagged')
        if is_flagged is not None:
            queryset = queryset.filter(is_flagged=is_flagged.lower() == 'true')

        return queryset.select_related('folder', 'thread')

    def get_serializer_class(self):
        if self.action == 'retrieve':
            return MailMessageDetailSerializer
        return MailMessageListSerializer

    # MAIL-159: message creation is not a supported API operation — messages are
    # fetched from the mail server, never created via this endpoint. The DRF
    # ModelViewSet default `create` used the read-only list serializer and then
    # raised an unhandled IntegrityError on an empty body, surfacing an HTML 500
    # page. Fail-soft: return a structured JSON 4xx instead.
    def create(self, request):
        return Response(
            {'error': 'Message creation via the API is not supported; messages are fetched from the mail server'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    def list(self, request):
        """Get messages from mail server."""
        from .services.mail_service import get_mail_service, MailServiceError

        account_id = request.query_params.get('account_id')
        account, error_response = resolve_account_for_request(request.user, account_id)
        if error_response:
            return error_response

        folder_id = request.query_params.get('folder')
        folder_role = request.query_params.get('folder_role')

        mail_service = get_mail_service(request)

        # MAIL-55: a local MailFolder UUID is a documented identifier — resolve
        # it to the server-side mailbox instead of passing the raw UUID to the
        # mail server (which used to 500).
        resolved_folder = None
        if folder_id:
            try:
                resolved_folder = MailFolder.objects.filter(account=account, id=folder_id).first()
            except Exception:  # not a UUID — treat as a server-side id/name
                resolved_folder = None
            if resolved_folder is not None:
                if account.incoming_server_type == 'jmap' and not resolved_folder.jmap_mailbox_id:
                    return Response(
                        {'error': 'Folder is not synced to the mail server yet'},
                        status=status.HTTP_409_CONFLICT,
                    )
                folder_id = resolved_folder.jmap_mailbox_id or resolved_folder.name

        try:
            # If folder_role provided, look up folder by role
            if not folder_id and folder_role:
                folders = mail_service.sync_folders(account)
                for f in folders:
                    if f.get('role') == folder_role:
                        folder_id = f.get('jmap_mailbox_id') or f.get('name')
                        break

            if not folder_id:
                return Response({'error': 'folder or folder_role parameter is required'}, status=400)

            result = mail_service.sync_messages(account, folder_id)

            # MAIL-55: apply the account's active filters when listing the
            # inbox, so a matching message is moved/marked the first time it is
            # seen — filters were previously stored but never evaluated.
            is_inbox = folder_role == 'inbox' or (resolved_folder is not None and resolved_folder.role == 'inbox')
            if is_inbox and account.incoming_server_type == 'jmap':
                from .services.filter_engine import apply_filters_to_messages
                from .tasks import _get_stalwart_client
                try:
                    applied = apply_filters_to_messages(
                        account,
                        [
                            {
                                **m,
                                'id': m.get('jmap_email_id') or m.get('uid'),
                                # MAIL-64: carry the known folder context so
                                # folder-role / has-attachment rules can match
                                # from the live-listing path (previously only
                                # the fetch dict fields were forwarded).
                                'folder': resolved_folder.name if resolved_folder else '',
                                'folder_role': resolved_folder.role if resolved_folder else (folder_role or ''),
                                'folder_name': resolved_folder.name if resolved_folder else '',
                                'role': resolved_folder.role if resolved_folder else (folder_role or ''),
                            }
                            for m in result.get('messages', [])
                        ],
                        source_mailbox_id=folder_id,
                        client=_get_stalwart_client(account),
                    )
                except Exception as e:
                    applied = {}
                    logger.warning("MAIL-55: filter application failed: %s", safe_exception_text(e))
                if applied:
                    moved = {mid for mid, oc in applied.items() if oc.get('moved_to') is not None}
                    kept = []
                    for m in result.get('messages', []):
                        mid = m.get('jmap_email_id') or m.get('uid')
                        if mid in moved:
                            continue
                        if mid in applied and applied[mid].get('mark_read'):
                            m = {**m, 'is_unread': False}
                        kept.append(m)
                    result = {**result, 'messages': kept, 'total': max(0, result.get('total', len(kept)) - len(moved))}

            # Transform to API format
            messages = []
            for msg in result.get('messages', []):
                messages.append({
                    'id': msg.get('jmap_email_id') or msg.get('uid'),
                    'subject': msg.get('subject', '(No subject)'),
                    'from_address': msg.get('from_address', ''),
                    'from_name': msg.get('from_name', ''),
                    'to_addresses': msg.get('to_addresses', []),
                    'preview': msg.get('preview', ''),
                    'is_unread': msg.get('is_unread', True),
                    'is_flagged': msg.get('is_flagged', False),
                    'is_answered': False,
                    'has_attachments': msg.get('has_attachments', False),
                    'attachment_count': 0,
                    'date_sent': msg.get('date_sent'),
                    'date_received': msg.get('date_received'),
                    'folder_id': folder_id,
                    'thread_id': msg.get('jmap_thread_id'),
                    'account_id': str(account.id),
                })

            total = result.get('total')
            if total is None:
                total = len(messages)
            else:
                total = max(int(total), len(messages))

            return Response({
                'count': total,
                'next': None,
                'previous': None,
                'results': messages
            })

        except MailServiceError as e:
            logger.error("Failed to fetch messages: %s", safe_exception_text(e))
            return generic_mail_error_response('Could not fetch messages')

    def retrieve(self, request, pk=None):
        """Get a single message with full details."""
        from .services.mail_service import get_mail_service, MailServiceError

        account_id = request.query_params.get('account_id')
        folder_id = request.query_params.get('folder')

        account, error_response = resolve_account_for_request(request.user, account_id)
        if error_response:
            return error_response

        mail_service = get_mail_service(request)

        # MAIL-158: search/list surface the local DB UUID pk (MailMessage.id),
        # but the provider fetch requires the server-side JMAP email id or IMAP
        # UID. Resolve a local UUID pk to its provider id before fetching;
        # fall through to the raw pk so a direct provider id still works and an
        # unknown id still 404s without enumerating.
        provider_message_id = pk
        fetch_folder_id = folder_id
        try:
            local_pk = uuid.UUID(str(pk))
        except (TypeError, ValueError):
            local_pk = None
        if local_pk is not None:
            local_msg = MailMessage.objects.filter(account=account, id=local_pk).first()
            if local_msg is not None:
                if account.incoming_server_type == 'imap':
                    provider_message_id = local_msg.imap_uid or pk
                    # IMAP requires a folder; prefer the row's own folder ref
                    # when the caller did not scope the request.
                    if not fetch_folder_id and local_msg.folder_id:
                        fetch_folder_id = server_folder_ref(local_msg.folder)
                elif local_msg.jmap_email_id:
                    provider_message_id = local_msg.jmap_email_id

        try:
            email = mail_service.get_message(account, provider_message_id, fetch_folder_id)

            if not email:
                return Response({'error': 'Message not found'}, status=404)

            return Response(email)

        except MailServiceError as e:
            logger.error("Failed to fetch message: %s", safe_exception_text(e))
            return generic_mail_error_response('Could not fetch message')

    def _resolve_source_folder(self, account, from_folder, default_folder_id=None):
        """Resolve a ``from_folder`` selector to a server-side folder ref.

        Accepts a local ``MailFolder`` UUID, a canonical role (inbox, sent,
        trash, junk, archive), or a raw server mailbox id/name. Returns the
        ``default_folder_id`` when no selector is given, or None when nothing
        resolves. MAIL-143: without this, a role-based selector (e.g. ``sent``)
        was passed through raw and a missing selector silently fell back to
        Inbox, so deleting from Sent moved the wrong membership.
        """
        from django.core.exceptions import ValidationError

        if not from_folder:
            return default_folder_id
        try:
            folder_obj = MailFolder.objects.filter(account=account, id=from_folder).first()
        except (ValidationError, TypeError, ValueError):
            folder_obj = None
        if folder_obj is not None:
            return server_folder_ref(folder_obj)
        role_folder = MailFolder.objects.filter(account=account, role=from_folder).first()
        if role_folder is not None:
            return server_folder_ref(role_folder)
        return from_folder

    @action(detail=False, methods=['post'])
    def move(self, request):
        """Move messages to a different folder."""
        from .services.mail_service import get_mail_service, MailServiceError

        serializer = MailMessageMoveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        account_id = serializer.validated_data.get('account_id')
        account, error_response = resolve_account_for_request(request.user, account_id)
        if error_response:
            return error_response

        message_ids = serializer.validated_data['message_ids']
        folder_id = serializer.validated_data.get('folder_id')
        folder_role = serializer.validated_data.get('folder_role')
        from_folder = serializer.validated_data.get('from_folder', '')

        mail_service = get_mail_service(request)

        try:
            # Get folders to find target
            folders = mail_service.sync_folders(account)
            reconcile_provider_folders(account, folders)
            target_folder = None
            target_folder_obj = None
            inbox_folder = None

            for f in folders:
                if folder_role and f.get('role') == folder_role:
                    target_folder = f.get('jmap_mailbox_id') or f.get('name')
                if f.get('role') == 'inbox':
                    inbox_folder = f.get('jmap_mailbox_id') or f.get('name')

            if folder_id:
                target_folder_obj = MailFolder.objects.filter(account=account, id=folder_id).first()
                if target_folder_obj is None:
                    return Response({'error': 'Target folder not found'}, status=404)
                target_folder = server_folder_ref(target_folder_obj)
            elif folder_role:
                target_folder_obj = MailFolder.objects.filter(account=account, role=folder_role).first()

            if not target_folder:
                return Response({'error': 'Target folder not found'}, status=404)

            # MAIL-143: resolve the source folder the same way as the target —
            # a local MailFolder UUID, a canonical role (sent/inbox/trash/...),
            # or a raw server mailbox ref. Previously a role was passed through
            # raw and a missing selector silently fell back to Inbox, which made
            # "delete from Sent" move the wrong membership (message stayed in
            # Sent and was added to Trash).
            source_folder = self._resolve_source_folder(account, from_folder, inbox_folder)
            if not source_folder:
                return Response({'error': 'Could not determine source folder'}, status=400)

            result = mail_service.move_messages(
                account,
                message_ids,
                source_folder,
                target_folder
            )
            moved_ids = result.get('moved', [])
            if target_folder_obj is not None and moved_ids:
                valid_uuid_ids = []
                for message_id in moved_ids:
                    try:
                        valid_uuid_ids.append(uuid.UUID(str(message_id)))
                    except (TypeError, ValueError):
                        pass
                message_filter = (
                    Q(jmap_email_id__in=moved_ids) |
                    Q(imap_uid__in=moved_ids) |
                    Q(id__in=valid_uuid_ids)
                )
                # MAIL-162: capture prior per-message state for bounded undo
                # BEFORE the folder update mutates the rows. Keyed by the
                # server identifier (jmap_email_id / imap_uid) so it matches
                # the moved_ids returned by the service.
                prior_rows = MailMessage.objects.filter(
                    account=account
                ).filter(message_filter).values(
                    'id', 'jmap_email_id', 'imap_uid', 'folder_id', 'is_flagged', 'is_unread',
                )
                prior_state = {
                    (row['jmap_email_id'] or row['imap_uid'] or str(row['id'])): {
                        'folder_id': str(row['folder_id']) if row['folder_id'] else None,
                        'is_flagged': row['is_flagged'],
                        'is_unread': row['is_unread'],
                    }
                    for row in prior_rows
                }
                with transaction.atomic():
                    affected = list(
                        MailMessage.objects
                        .filter(account=account)
                        .filter(message_filter)
                        .values_list('folder_id', flat=True)
                    )
                    if affected:
                        MailMessage.objects.filter(account=account).filter(message_filter).update(folder=target_folder_obj)
                        refresh_folder_counts_from_messages(account, set(affected) | {target_folder_obj.id})
                undo_entry = _record_undo_log(
                    request, account, MailUndoLog.OP_MOVE,
                    moved_ids, prior_state,
                    target_folder_id=target_folder_obj.id,
                )

            return Response({
                'status': 'moved',
                'moved': moved_ids,
                'failed': result.get('failed', {}),
                'target_folder': target_folder,
                'undo_id': str(undo_entry.id) if undo_entry else None,
            })

        except MailServiceError as e:
            logger.error("Failed to move messages: %s", safe_exception_text(e))
            return generic_mail_error_response('Could not move messages')

    @action(detail=False, methods=['post'])
    def flag(self, request):
        """Update message flags (read/unread, flagged)."""
        from .services.mail_service import get_mail_service, MailServiceError

        serializer = MailMessageFlagSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        account_id = serializer.validated_data.get('account_id')
        account, error_response = resolve_account_for_request(request.user, account_id)
        if error_response:
            return error_response

        mail_service = get_mail_service(request)

        try:
            message_ids = serializer.validated_data['message_ids']
            folder_id = serializer.validated_data.get('folder_id')
            is_unread = serializer.validated_data.get('is_unread')
            is_flagged = serializer.validated_data.get('is_flagged')

            # MAIL-162: capture prior per-message flag state for bounded undo.
            # folder_id is required for IMAP flag updates (the default server
            # type), so record it per message for the undo path.
            prior_rows = MailMessage.objects.filter(
                account=account,
                id__in=[mid for mid in message_ids if _is_uuid(mid)],
            ).values('id', 'folder_id', 'is_flagged', 'is_unread')
            prior_state = {
                str(row['id']): {
                    'folder_id': str(row['folder_id']) if row['folder_id'] else None,
                    'is_flagged': row['is_flagged'],
                    'is_unread': row['is_unread'],
                }
                for row in prior_rows
            }

            result = mail_service.set_message_flags(
                account,
                message_ids,
                folder_id=folder_id,
                is_unread=is_unread,
                is_flagged=is_flagged
            )

            undo_entry = _record_undo_log(
                request, account, MailUndoLog.OP_FLAG,
                list(prior_state.keys()), prior_state,
            )

            return Response({
                'status': 'updated',
                'count': len(message_ids),
                'undo_id': str(undo_entry.id) if undo_entry else None,
            })

        except MailServiceError as e:
            logger.error("Failed to update flags: %s", safe_exception_text(e))
            return generic_mail_error_response('Could not update message flags')

    @action(detail=False, methods=['post'])
    def trash(self, request):
        """Move messages to trash."""
        from .services.mail_service import get_mail_service, MailServiceError

        message_ids = request.data.get('message_ids', [])
        account_id = request.data.get('account_id')
        from_folder = request.data.get('from_folder')

        if not message_ids:
            return Response({'error': 'message_ids is required'}, status=400)

        account, error_response = resolve_account_for_request(request.user, account_id)
        if error_response:
            return error_response

        mail_service = get_mail_service(request)

        try:
            folders = mail_service.sync_folders(account)
            trash_folder = None
            inbox_folder = None

            for f in folders:
                if f.get('role') == 'trash':
                    trash_folder = f.get('jmap_mailbox_id') or f.get('name')
                if f.get('role') == 'inbox':
                    inbox_folder = f.get('jmap_mailbox_id') or f.get('name')

            if not trash_folder:
                return Response({'error': 'Trash folder not found'}, status=404)

            # MAIL-143: resolve the source folder (local UUID, role, or server
            # ref) instead of silently falling back to Inbox. The UI sends the
            # folder role for role-based folders (sent/trash/...); without this
            # resolution, deleting from Sent moved the wrong membership and the
            # message stayed in Sent while being added to Trash.
            source_folder = self._resolve_source_folder(account, from_folder, inbox_folder)
            if not source_folder:
                return Response({'error': 'Could not determine source folder'}, status=400)

            result = mail_service.move_messages(
                account,
                message_ids,
                source_folder,
                trash_folder
            )

            # MAIL-162: record trash as a reversible op for bounded undo.
            moved_ids = result.get('moved', [])
            if moved_ids:
                valid_uuid_ids = []
                for message_id in moved_ids:
                    try:
                        valid_uuid_ids.append(uuid.UUID(str(message_id)))
                    except (TypeError, ValueError):
                        pass
                message_filter = (
                    Q(jmap_email_id__in=moved_ids) |
                    Q(imap_uid__in=moved_ids) |
                    Q(id__in=valid_uuid_ids)
                )
                prior_rows = MailMessage.objects.filter(
                    account=account
                ).filter(message_filter).values(
                    'id', 'jmap_email_id', 'imap_uid', 'folder_id', 'is_flagged', 'is_unread',
                )
                prior_state = {
                    (row['jmap_email_id'] or row['imap_uid'] or str(row['id'])): {
                        'folder_id': str(row['folder_id']) if row['folder_id'] else None,
                        'is_flagged': row['is_flagged'],
                        'is_unread': row['is_unread'],
                    }
                    for row in prior_rows
                }
                trash_folder_obj = MailFolder.objects.filter(
                    account=account, role='trash',
                ).first()
                undo_entry = _record_undo_log(
                    request, account, MailUndoLog.OP_TRASH,
                    moved_ids, prior_state,
                    target_folder_id=trash_folder_obj.id if trash_folder_obj else None,
                )

            return Response({
                'status': 'trashed',
                'moved': result.get('moved', []),
                'failed': result.get('failed', {}),
                'undo_id': str(undo_entry.id) if undo_entry else None,
            })

        except MailServiceError as e:
            logger.error("Failed to trash messages: %s", safe_exception_text(e))
            return generic_mail_error_response('Could not trash messages')

    @action(detail=False, methods=['post'])
    def delete(self, request):
        """Permanently delete messages."""
        from .services.mail_service import get_mail_service, MailServiceError

        message_ids = request.data.get('message_ids', [])
        account_id = request.data.get('account_id')
        folder_id = request.data.get('folder_id')

        if not message_ids:
            return Response({'error': 'message_ids is required'}, status=400)

        account, error_response = resolve_account_for_request(request.user, account_id)
        if error_response:
            return error_response

        mail_service = get_mail_service(request)

        try:
            result = mail_service.delete_messages(
                account,
                message_ids,
                folder_id=folder_id
            )

            return Response({
                'status': 'deleted',
                'deleted': result.get('deleted', []),
                'failed': result.get('failed', {})
            })

        except MailServiceError as e:
            logger.error("Failed to delete messages: %s", safe_exception_text(e))
            return generic_mail_error_response('Could not delete messages')

    @action(detail=False, methods=['post'])
    def undo(self, request):
        """MAIL-162: undo a reversible bulk op (move/flag/trash) within the window.

        Body: {"undo_id": "<uuid>"} — the id returned by the original bulk op.
        Reverses the op and marks the log entry undone. Permanent delete is not
        undoable (no log entry is ever created for it).
        """
        from .services.mail_service import get_mail_service, MailServiceError

        undo_id = request.data.get('undo_id')
        if not undo_id:
            return Response({'error': 'undo_id is required'}, status=400)

        try:
            entry = MailUndoLog.objects.get(id=undo_id, user_id=request.user.id)
        except (MailUndoLog.DoesNotExist, ValueError):
            return Response({'error': 'Undo entry not found'}, status=404)

        if entry.is_undone():
            return Response({'error': 'Undo entry already applied'}, status=409)
        if entry.is_expired():
            return Response({'error': 'Undo window expired'}, status=410)

        account = entry.account
        mail_service = get_mail_service(request)
        undone = []
        failed = {}

        try:
            if entry.op_type in (MailUndoLog.OP_MOVE, MailUndoLog.OP_TRASH):
                # Move each message back to its prior folder.
                target_folder_obj = None
                if entry.target_folder_id:
                    target_folder_obj = MailFolder.objects.filter(
                        account=account, id=entry.target_folder_id,
                    ).first()
                source_folder = server_folder_ref(target_folder_obj) if target_folder_obj else None
                for message_id in entry.message_ids:
                    prior = entry.prior_state.get(str(message_id), {})
                    prior_folder_id = prior.get('folder_id')
                    if not prior_folder_id:
                        failed[str(message_id)] = 'no prior folder recorded'
                        continue
                    prior_folder_obj = MailFolder.objects.filter(
                        account=account, id=prior_folder_id,
                    ).first()
                    if prior_folder_obj is None:
                        failed[str(message_id)] = 'prior folder not found'
                        continue
                    try:
                        result = mail_service.move_messages(
                            account,
                            [message_id],
                            source_folder or str(target_folder_obj.id),
                            server_folder_ref(prior_folder_obj),
                        )
                        if result.get('moved'):
                            undone.append(message_id)
                        else:
                            failed[str(message_id)] = result.get('failed', {}) or 'move failed'
                    except MailServiceError as e:
                        failed[str(message_id)] = safe_exception_text(e)
                # Refresh the local cache folder for restored messages.
                if undone:
                    valid_uuids = [mid for mid in undone if _is_uuid(mid)]
                    message_filter = (
                        Q(jmap_email_id__in=undone) |
                        Q(imap_uid__in=undone) |
                        Q(id__in=valid_uuids)
                    )
                    prior_folder_map = {
                        str(mid): entry.prior_state.get(str(mid), {}).get('folder_id')
                        for mid in undone
                    }
                    for row in MailMessage.objects.filter(
                        account=account
                    ).filter(message_filter).values('id', 'jmap_email_id', 'imap_uid'):
                        key = row['jmap_email_id'] or row['imap_uid'] or str(row['id'])
                        prior_folder_id = prior_folder_map.get(key)
                        prior_folder_obj = MailFolder.objects.filter(
                            account=account, id=prior_folder_id,
                        ).first()
                        if prior_folder_obj is not None:
                            MailMessage.objects.filter(
                                account=account, id=row['id'],
                            ).update(folder=prior_folder_obj)
            elif entry.op_type == MailUndoLog.OP_FLAG:
                # Restore prior flag/unread state per message. folder_id is
                # required for IMAP flag updates (the default server type), so
                # pass the recorded prior folder (MAIL-162 P2 fix).
                for message_id, prior in entry.prior_state.items():
                    if not _is_uuid(message_id):
                        failed[message_id] = 'not a local message id'
                        continue
                    try:
                        mail_service.set_message_flags(
                            account,
                            [message_id],
                            folder_id=prior.get('folder_id'),
                            is_unread=prior.get('is_unread'),
                            is_flagged=prior.get('is_flagged'),
                        )
                        undone.append(message_id)
                    except MailServiceError as e:
                        failed[message_id] = safe_exception_text(e)
            else:
                return Response({'error': 'Unsupported undo op type'}, status=400)

            entry.undone_at = timezone.now()
            entry.save(update_fields=['undone_at'])
            return Response({
                'status': 'undone',
                'op_type': entry.op_type,
                'undone': undone,
                'failed': failed,
            })

        except MailServiceError as e:
            logger.error("Failed to undo %s: %s", entry.op_type, safe_exception_text(e))
            return generic_mail_error_response('Could not undo operation')

    def _search_accounts_for_request(self, user, data):
        """Resolve the account scope while preserving legacy default-account search."""
        if data.get('account_ids'):
            accounts = list(MailAccount.objects.filter(user_id=user.id, id__in=data['account_ids'], is_active=True))
            # MAIL-46 security contract: an explicitly-requested scope the
            # caller does not own returns EMPTY results — never a 404 that
            # could confirm a foreign account id exists.
            return accounts, None
        if data.get('scope') in ('all', 'unified'):
            return list(MailAccount.objects.filter(user_id=user.id, is_active=True)), None
        account, error_response = resolve_account_for_request(user, data.get('account_id'))
        if error_response:
            return [], error_response
        return [account], None

    def _decode_search_cursor(self, cursor):
        if not cursor:
            return None
        try:
            return json.loads(base64.urlsafe_b64decode(cursor.encode()).decode())
        except Exception:
            return None

    def _encode_search_cursor(self, message, sort='relevance'):
        payload = {
            'rank': float(getattr(message, 'rank', 0) or 0),
            'date_received': message.date_received.isoformat() if message.date_received else '',
            'id': str(message.id),
            'sort': sort,
        }
        return base64.urlsafe_b64encode(json.dumps(payload, separators=(',', ':')).encode()).decode()

    # MAIL-46: query tokens for the match_reason heuristic — strip websearch
    # operators/quotes so containment checks see plain terms.
    _SEARCH_TOKEN_STRIP = '"\'()+-'

    def _search_terms(self, query):
        terms = []
        for raw in (query or '').split():
            token = raw.strip(self._SEARCH_TOKEN_STRIP).lower()
            if token and token not in {'or', 'and', 'not'}:
                terms.append(token)
        return terms

    def _match_reasons(self, message, terms):
        """Best-effort per-hit explanation of WHERE the query matched, mirroring
        the FTS vector's fields (subject A / from B / recipients B /
        preview+body content). Falls back to ['content'] for stem/phrase
        matches containment can't see."""
        reasons = []
        subject = (message.subject or '').lower()
        sender = f"{message.from_address or ''} {message.from_name or ''}".lower()
        recipients = f"{message.to_addresses or ''} {message.cc_addresses or ''}".lower()
        content = f"{message.preview or ''} {getattr(message, 'body_text', '') or ''}".lower()
        for term in terms:
            if term in subject and 'subject' not in reasons:
                reasons.append('subject')
            if term in sender and 'from' not in reasons:
                reasons.append('from')
            if term in recipients and 'recipients' not in reasons:
                reasons.append('recipients')
            if term in content and 'content' not in reasons:
                reasons.append('content')
        return reasons or ['content']

    _SNIPPET_START = '\ue000'
    _SNIPPET_STOP = '\ue001'

    def _sanitize_snippet(self, raw):
        """HTML-escape the source text, then swap the DB-side sentinel
        delimiters for <mark> tags — the ONLY markup a snippet can contain is
        ours, regardless of what the message text held."""
        from django.utils.html import escape
        return (escape(raw or '')
                .replace(self._SNIPPET_START, '<mark>')
                .replace(self._SNIPPET_STOP, '</mark>'))

    def _fallback_snippet(self, message, terms, limit=200):
        """Non-Postgres snippet: an escaped window around the first term hit,
        with the hit wrapped in <mark>. Postgres path uses SearchHeadline."""
        source = message.preview or message.subject or ''
        lowered = source.lower()
        for term in terms:
            at = lowered.find(term)
            if at >= 0:
                start = max(0, at - 60)
                before = self._sanitize_snippet(source[start:at])
                hit = self._sanitize_snippet(source[at:at + len(term)])
                after = self._sanitize_snippet(source[at + len(term):start + limit])
                return f'{before}<mark>{hit}</mark>{after}'
        return self._sanitize_snippet(source[:limit])

    @action(detail=False, methods=['post', 'get'])
    def search(self, request):
        """Search cached mail metadata with Postgres FTS and keyset pagination."""
        input_data = request.data if request.method == 'POST' else request.query_params
        serializer = MailSearchSerializer(data=input_data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        accounts, error_response = self._search_accounts_for_request(request.user, data)
        if error_response:
            return error_response
        if not accounts:
            return Response({'count': 0, 'next': None, 'next_cursor': None, 'previous': None, 'results': []})

        queryset = MailMessage.objects.filter(account__in=accounts).select_related('folder', 'thread', 'account')
        query = data['query']

        if connection.vendor == 'postgresql':
            search_query = SearchQuery(query, config='simple', search_type='websearch')
            # ts_rank returns float4. Its abbreviated wire decimal does not round-trip
            # through a JSON/Python float8 cursor. Cast before both ordering and
            # comparison so equal-rank rows cannot repeat or disappear on paging.
            queryset = queryset.annotate(rank=Cast(
                SearchRank(F('search_vector'), search_query), output_field=FloatField(),
            )).filter(search_vector=search_query)
        else:
            # Test/development fallback for non-Postgres databases; production uses FTS.
            queryset = queryset.filter(
                Q(subject__icontains=query) |
                Q(from_address__icontains=query) |
                Q(from_name__icontains=query) |
                Q(to_addresses__icontains=query) |
                Q(cc_addresses__icontains=query) |
                Q(preview__icontains=query)
            ).annotate(rank=Value(0.0, output_field=FloatField()))

        def _folder_alias_q(alias):
            alias_q = Q(folder__name__iexact=alias) | Q(folder__role__iexact=alias)
            try:
                alias_q |= Q(folder__id=uuid.UUID(str(alias)))
            except (TypeError, ValueError):
                pass
            return alias_q

        folder_id = data.get('folder_id')
        folder_alias = data.get('folder')
        folder_aliases = data.get('folders')
        if folder_aliases:
            # MAIL-46: multi-folder scope (names/roles/ids, OR-combined). The
            # legacy single `folder`/`folder_id` params keep their behavior.
            combined = Q(pk__in=[])
            for alias in folder_aliases:
                combined |= _folder_alias_q(alias)
            queryset = queryset.filter(combined)
        elif folder_id:
            queryset = queryset.filter(folder_id=folder_id)
        elif folder_alias:
            queryset = queryset.filter(_folder_alias_q(folder_alias))

        if data.get('thread_id'):
            queryset = queryset.filter(thread_id=data['thread_id'])

        if data.get('from_address'):
            queryset = queryset.filter(from_address__icontains=data['from_address'])
        if data.get('to_address'):
            queryset = queryset.filter(to_addresses__icontains=data['to_address'])
        if data.get('has_attachment') is not None:
            queryset = queryset.filter(has_attachments=data['has_attachment'])
        if data.get('is_unread') is not None:
            queryset = queryset.filter(is_unread=data['is_unread'])
        if data.get('is_flagged') is not None:
            queryset = queryset.filter(is_flagged=data['is_flagged'])
        if data.get('date_from'):
            queryset = queryset.filter(date_received__gte=data['date_from'])
        if data.get('date_to'):
            queryset = queryset.filter(date_received__lte=data['date_to'])

        sort = data.get('sort') or 'relevance'

        cursor = self._decode_search_cursor(data.get('cursor'))
        # A cursor minted under a different sort orders by different keys —
        # applying it would corrupt the keyset; ignore it instead.
        if cursor and cursor.get('sort', 'relevance') != sort:
            cursor = None
        if cursor:
            rank = cursor.get('rank', 0)
            received = cursor.get('date_received') or None
            last_id = cursor.get('id')
            if received and last_id:
                if sort == 'date_desc':
                    queryset = queryset.filter(
                        Q(date_received__lt=received) |
                        Q(date_received=received, id__lt=last_id)
                    )
                elif sort == 'date_asc':
                    queryset = queryset.filter(
                        Q(date_received__gt=received) |
                        Q(date_received=received, id__gt=last_id)
                    )
                else:
                    queryset = queryset.filter(
                        Q(rank__lt=rank) |
                        Q(rank=rank, date_received__lt=received) |
                        Q(rank=rank, date_received=received, id__lt=last_id)
                    )

        # MAIL-46: approximate total — an exact COUNT over a large FTS result
        # is unbounded work, so the count subquery is capped; total_capped
        # tells the client the real total is >= this value.
        TOTAL_CAP = 1000
        total = queryset.order_by().values('pk')[:TOTAL_CAP + 1].count()
        total_capped = total > TOTAL_CAP
        if total_capped:
            total = TOTAL_CAP

        include_snippets = data.get('include_snippets', True)
        terms = self._search_terms(query)
        if include_snippets and connection.vendor == 'postgresql':
            queryset = queryset.annotate(snippet_hl=SearchHeadline(
                'preview', search_query, config='simple',
                start_sel=self._SNIPPET_START, stop_sel=self._SNIPPET_STOP,
            ))

        ORDERINGS = {
            'relevance': ('-rank', '-date_received', '-id'),
            'date_desc': ('-date_received', '-id'),
            'date_asc': ('date_received', 'id'),
        }
        page_size = data.get('page_size') or 20
        rows = list(queryset.order_by(*ORDERINGS[sort])[:page_size + 1])
        has_next = len(rows) > page_size
        rows = rows[:page_size]
        next_cursor = self._encode_search_cursor(rows[-1], sort=sort) if has_next and rows else None
        payload = MailMessageListSerializer(rows, many=True).data
        for row_data, message in zip(payload, rows):
            row_data['score'] = float(getattr(message, 'rank', 0) or 0)
            row_data['match_reason'] = self._match_reasons(message, terms)
            if include_snippets:
                headline = getattr(message, 'snippet_hl', None)
                if headline and self._SNIPPET_START in headline:
                    row_data['snippet'] = self._sanitize_snippet(headline)
                else:
                    row_data['snippet'] = self._fallback_snippet(message, terms)
        return Response({
            'count': total,
            'total': total,
            'total_capped': total_capped,
            'next': next_cursor,
            'next_cursor': next_cursor,
            'previous': None,
            'results': payload,
        })


class MailThreadViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Mail thread (conversation) views.
    """
    serializer_class = MailThreadSerializer
    permission_classes = [IsAuthenticated]

    def _resolve_thread_folder(self, account, folder_id, folder_role):
        """Resolve folder/folder_role query params to a cached MailFolder.

        ``folder`` may be a local MailFolder UUID, a server-side mailbox id
        (jmap_mailbox_id), or a folder name — consistent with
        ``MailMessageViewSet``. ``folder_role`` is a canonical system role
        (inbox, drafts, sent, trash, junk, archive). Returns None when the
        selector matches no folder on this account.
        """
        if folder_id:
            q = Q(jmap_mailbox_id=folder_id) | Q(name=folder_id)
            try:
                uuid.UUID(str(folder_id))
                q = q | Q(id=folder_id)
            except (ValueError, AttributeError, TypeError):
                # Not a UUID — a server-side mailbox id/name; skip the id lookup.
                pass
            return (
                MailFolder.objects.filter(account=account)
                .filter(q)
                .first()
            )
        if folder_role:
            return MailFolder.objects.filter(account=account, role=folder_role).first()
        return None

    def get_queryset(self):
        account_id = self.request.query_params.get('account_id')
        if account_id:
            account = get_account_by_id(self.request.user, account_id)
        else:
            account = get_default_account(self.request.user)

        if not account:
            return MailThread.objects.none()

        queryset = MailThread.objects.filter(account=account)

        # MAIL-92: folder scoping. MailThread has no folder FK of its own, so
        # scope by the folders of the thread's cached messages. An unknown
        # folder yields an empty set here; list() turns that into an explicit
        # 400 so no silently-ignored filter parameter remains.
        folder_id = self.request.query_params.get('folder')
        folder_role = self.request.query_params.get('folder_role')
        if folder_id or folder_role:
            folder = self._resolve_thread_folder(account, folder_id, folder_role)
            if folder is None:
                return MailThread.objects.none()
            queryset = queryset.filter(messages__folder=folder).distinct()

        return queryset

    def list(self, request, *args, **kwargs):
        """List threads, validating folder scoping explicitly."""
        account_id = request.query_params.get('account_id')
        account, error_response = resolve_account_for_request(request.user, account_id)
        if error_response:
            return error_response

        folder_id = request.query_params.get('folder')
        folder_role = request.query_params.get('folder_role')
        if folder_id or folder_role:
            folder = self._resolve_thread_folder(account, folder_id, folder_role)
            if folder is None:
                return Response(
                    {
                        'error': 'Unknown folder: folder or folder_role does not '
                                 'match any folder on this account'
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
        return super().list(request, *args, **kwargs)


class MailDraftViewSet(viewsets.ModelViewSet):
    """
    Draft message management.
    """
    serializer_class = MailDraftSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        account_id = self.request.query_params.get('account_id')
        if account_id:
            account = get_account_by_id(self.request.user, account_id)
        else:
            account = get_default_account(self.request.user)

        if not account:
            return MailDraft.objects.none()

        return MailDraft.objects.filter(account=account)

    def create(self, request):
        """Create a new draft."""
        serializer = MailDraftSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        account_id = request.data.get('account_id')
        account, error_response = resolve_account_for_request(request.user, account_id)
        if error_response:
            return error_response

        draft = serializer.save(account=account)

        return Response(
            MailDraftSerializer(draft).data,
            status=status.HTTP_201_CREATED
        )

    def update(self, request, *args, **kwargs):
        """Atomically compare-and-save a complete draft revision."""
        from django.db import transaction

        try:
            base_revision = int(request.data.get('base_revision'))
        except (TypeError, ValueError):
            return Response(
                {'code': 'base_revision_required', 'detail': 'base_revision must be an integer'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        partial = kwargs.pop('partial', False)
        with transaction.atomic():
            draft = self.get_queryset().select_for_update().filter(pk=kwargs.get('pk')).first()
            if draft is None:
                return Response(status=status.HTTP_404_NOT_FOUND)

            serializer = self.get_serializer(draft, data=request.data, partial=partial)
            serializer.is_valid(raise_exception=True)
            submitted = {
                key: value for key, value in serializer.validated_data.items()
                if key != 'base_revision'
            }
            serializer.validated_data.pop('base_revision', None)
            if base_revision != draft.revision:
                # Lost-response autosave retries are idempotent when the
                # submitted representation is already the durable revision.
                if all(getattr(draft, key) == value for key, value in submitted.items()):
                    return Response(self.get_serializer(draft).data)
                return Response(
                    {
                        'code': 'draft_revision_conflict',
                        'detail': 'Draft changed since base_revision',
                        'base_revision': base_revision,
                        'current_revision': draft.revision,
                    },
                    status=status.HTTP_409_CONFLICT,
                )
            draft.revision += 1
            serializer.save(revision=draft.revision)
            return Response(self.get_serializer(draft).data)

    @action(detail=True, methods=['post'])
    def send(self, request, pk=None):
        """Send a draft as an email with exactly-once semantics (MAIL-59)."""
        from .services.mail_service import get_mail_service, MailServiceError

        # Generate or use client_message_id
        client_message_id = request.data.get('client_message_id')
        if client_message_id:
            client_message_id = uuid.UUID(client_message_id) if isinstance(client_message_id, str) else client_message_id
        draft = self.get_queryset().filter(pk=pk).first()
        if draft is None and client_message_id:
            # A successful send deletes the draft. Preserve its UUID in the
            # outbox result so a lost-response retry can still replay rather
            # than returning 404 or invoking the provider again.
            existing = OutboxMessage.objects.filter(
                account__in=get_user_accounts(request.user),
                client_message_id=client_message_id,
                result__draft_id=str(pk),
            ).first()
            if existing is not None and existing.status == 'sent' and existing.result:
                return Response({
                    'status': 'sent',
                    'message': 'Draft sent successfully (idempotent replay)',
                    'email_id': existing.result.get('email_id') or existing.result.get('message_id'),
                })
        if draft is None:
            raise NotFound('No MailDraft matches the given query.')

        # MAIL-192: reap rows stuck non-terminal past the interactive bound
        # with provider I/O provably unstarted — a process death mid-queue
        # (rebuild/restart) must not lock the account out of sending for an
        # hour waiting for background cleanup.
        stale_bound = timezone.now() - timedelta(minutes=15)
        OutboxMessage.objects.filter(
            account=draft.account,
            status__in=['queued', 'sending'],
        ).exclude(provider_started=True).filter(
            updated_at__lt=stale_bound,
        ).update(status='failed', error='Stale in-progress send reaped (MAIL-192)')
        if client_message_id:
            existing = OutboxMessage.objects.filter(
                account=draft.account,
                client_message_id=client_message_id
            ).first()
            if existing is not None:
                if existing.status == 'sent' and existing.result:
                    draft.delete()
                    return Response({
                        'status': 'sent',
                        'message': 'Draft sent successfully (idempotent replay)',
                        'email_id': existing.result.get('email_id') or existing.result.get('message_id'),
                    })
                elif existing.status == 'failed':
                    if not _consume_failed_preprovider_outbox(existing):
                        return Response({
                            'status': 'error',
                            'error': existing.error or 'Send failed',
                        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
                    # retryable auth/exchange failure — fall through to re-create outbox
                else:
                    return Response({
                        'status': 'conflict',
                        'error': 'A send request for this draft is already in progress',
                    }, status=status.HTTP_409_CONFLICT)
        else:
            client_message_id = uuid.uuid4()

        # MAIL-74: exchange before outbox so auth failure never poisons CMI.
        preflight = _preflight_managed_identity_exchange(request, draft.account)
        if preflight is not None:
            return preflight

        # Create outbox entry (with IntegrityError guard for concurrent requests)
        try:
            outbox = OutboxMessage.objects.create(
                account=draft.account,
                client_message_id=client_message_id,
                status='queued',
                provider_started=False,
                to_addresses=draft.to_addresses,
                cc_addresses=draft.cc_addresses,
                bcc_addresses=draft.bcc_addresses,
                subject=draft.subject,
                body_text=draft.body_text,
                body_html=draft.body_html,
                draft=draft,
            )
        except IntegrityError:
            return Response({
                'status': 'conflict',
                'error': 'A send request with this client_message_id is already in progress',
                'client_message_id': str(client_message_id),
            }, status=status.HTTP_409_CONFLICT)

        try:
            mail_service = get_mail_service(request)
            outbox.status = 'sending'
            outbox.save(update_fields=['status', 'updated_at'])

            # From this write onward, any failure is potentially an ambiguous
            # provider submission and MAIL-59 must not automatically re-arm.
            outbox.provider_started = True
            outbox.save(update_fields=['provider_started', 'updated_at'])

            if draft.account.incoming_server_type == 'jmap':
                result = mail_service.send_email_jmap(
                    account=draft.account,
                    to_addresses=draft.to_addresses,
                    subject=draft.subject,
                    body_text=draft.body_text,
                    body_html=draft.body_html,
                    cc_addresses=draft.cc_addresses,
                    bcc_addresses=draft.bcc_addresses,
                )
            elif draft.account.outgoing_host:
                result = mail_service.send_email(
                    account=draft.account,
                    to_addresses=draft.to_addresses,
                    subject=draft.subject,
                    body_text=draft.body_text,
                    body_html=draft.body_html,
                    cc_addresses=draft.cc_addresses,
                    bcc_addresses=draft.bcc_addresses,
                )
            else:
                raise MailServiceError('No outgoing SMTP server configured for account')

            # Success
            result = {**result, 'draft_id': str(draft.id)}
            outbox.status = 'sent'
            outbox.result = result
            outbox.sent_at = timezone.now()
            outbox.save(update_fields=['status', 'result', 'sent_at', 'updated_at'])

            draft.delete()

            return Response({
                'status': 'sent',
                'message': 'Draft sent successfully',
                'email_id': result.get('email_id') or result.get('message_id'),
            })

        except MailServiceError as e:
            outbox.status = 'failed'
            outbox.error = safe_exception_text(e)
            outbox.save(update_fields=['status', 'error', 'updated_at'])

            return Response({
                'status': 'error',
                'error': safe_exception_text(e),
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        except Exception as e:
            # MAIL-192: a non-service failure (validation, timeout, driver
            # error) must still drive the outbox row terminal — a stuck
            # non-terminal row poisons every retry via the in-progress guard.
            outbox.status = 'failed'
            outbox.error = safe_exception_text(e)
            outbox.save(update_fields=['status', 'error', 'updated_at'])
            return Response({
                'status': 'error',
                'error': safe_exception_text(e),
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class MailSendViewSet(viewsets.ViewSet):
    """
    Send email endpoint.
    """
    permission_classes = [IsAuthenticated]
    # MAIL-164: expose the serializer so drf_spectacular can document the
    # request body. A bare ViewSet has no serializer_class, so the OpenAPI
    # schema previously showed POST /api/send/ with NO requestBody.
    serializer_class = MailSendSerializer

    def get_serializer(self, *args, **kwargs):
        """Return a MailSendSerializer instance (schema + validation)."""
        kwargs.setdefault('context', {'request': getattr(self, 'request', None)})
        return MailSendSerializer(*args, **kwargs)

    def retrieve(self, request, pk=None):
        """Surface the durable send and provider delivery state."""
        outbox = OutboxMessage.objects.filter(
            account__in=get_user_accounts(request.user), client_message_id=pk,
        ).first()
        if outbox is None:
            raise NotFound('No send record matches the client_message_id.')
        return Response({
            'client_message_id': str(outbox.client_message_id),
            'send_status': outbox.status,
            'delivery_status': outbox.delivery_status,
            'delivery_diagnostic': outbox.delivery_diagnostic,
            'delivery_updated_at': outbox.delivery_updated_at,
            'sent_at': outbox.sent_at,
        })

    def create(self, request):
        """Send a new email with exactly-once semantics (MAIL-59)."""
        from .services.mail_service import get_mail_service, MailServiceError

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        account_id = serializer.validated_data.get('account_id')
        account, error_response = resolve_account_for_request(request.user, account_id)
        if error_response:
            return error_response

        # MAIL-119: the browser sends the opaque message key it opened. Resolve
        # it only through the selected owned account and derive RFC threading
        # metadata server-side instead of trusting caller-supplied headers.
        reply_to_message_id = serializer.validated_data.get('reply_to_message_id')
        if reply_to_message_id:
            mail_service = get_mail_service(request)
            reply_to_folder_id = serializer.validated_data.get('reply_to_folder_id')
            if account.incoming_server_type == 'imap' and not reply_to_folder_id:
                return Response(
                    {
                        'code': 'reply_message_not_found',
                        'detail': 'No reply source matches this account.',
                    },
                    status=status.HTTP_404_NOT_FOUND,
                )
            try:
                if account.incoming_server_type == 'imap':
                    reply_message = mail_service.get_message(
                        account, reply_to_message_id, reply_to_folder_id,
                    )
                else:
                    reply_message = mail_service.get_message(account, reply_to_message_id)
            except MailServiceError:
                reply_message = None
            if reply_message is None:
                return Response(
                    {
                        'code': 'reply_message_not_found',
                        'detail': 'No reply source matches this account.',
                    },
                    status=status.HTTP_404_NOT_FOUND,
                )

            parent_message_id, references = _normalise_reply_message_ids(reply_message)
            if not parent_message_id:
                return Response(
                    {
                        'code': 'reply_message_missing_message_id',
                        'detail': 'The reply source has no RFC Message-ID.',
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
            serializer.validated_data['in_reply_to'] = parent_message_id
            serializer.validated_data['references'] = references
            if not serializer.validated_data['to_addresses']:
                recipient = reply_message.get('reply_to') or reply_message.get('from_address')
                if not recipient:
                    return Response(
                        {
                            'code': 'reply_recipient_not_found',
                            'detail': 'The reply source has no reply recipient.',
                        },
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                recipient = str(recipient).strip()
                try:
                    validate_email(recipient)
                except DjangoValidationError:
                    return Response(
                        {
                            'code': 'reply_recipient_invalid',
                            'detail': 'The reply source has no valid reply recipient.',
                        },
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                serializer.validated_data['to_addresses'] = [recipient]

        # ── Idempotency check ────────────────────────────────────────────
        client_message_id = serializer.validated_data.get('client_message_id')
        if client_message_id is None:
            client_message_id = uuid.uuid4()

        existing = OutboxMessage.objects.filter(
            account=account,
            client_message_id=client_message_id
        ).first()
        if existing is not None:
            if existing.status == 'sent' and existing.result:
                return Response({
                    'status': 'sent',
                    'from': account.email_address,
                    'to': existing.to_addresses,
                    'subject': existing.subject,
                    'message': 'Email sent successfully (idempotent replay)',
                    'email_id': existing.result.get('email_id') or existing.result.get('message_id'),
                    'client_message_id': str(client_message_id),
                })
            elif existing.status == 'failed':
                if not _consume_failed_preprovider_outbox(existing):
                    return Response({
                        'status': 'error',
                        'error': existing.error or 'Send failed',
                        'client_message_id': str(client_message_id),
                    }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
                # retryable auth/exchange failure — fall through to re-create outbox
            else:
                # queued or sending — concurrent request
                return Response({
                    'status': 'conflict',
                    'error': 'A send request with this client_message_id is already in progress',
                    'client_message_id': str(client_message_id),
                }, status=status.HTTP_409_CONFLICT)

        # MAIL-74: exchange before outbox so auth failure never poisons CMI.
        preflight = _preflight_managed_identity_exchange(request, account)
        if preflight is not None:
            return preflight

        # Serialize quota admission per account. The lock is released before
        # provider I/O, but the durable outbox row is committed while held so
        # concurrent requests cannot all observe the same remaining slot.
        try:
            with transaction.atomic():
                MailAccount.objects.select_for_update().only('pk').get(pk=account.pk)

                # A concurrent request may have inserted this CMI after the
                # fast replay check above but before this account lock.
                if OutboxMessage.objects.filter(
                    account=account,
                    client_message_id=client_message_id,
                ).exists():
                    return Response({
                        'status': 'conflict',
                        'error': 'A send request with this client_message_id is already in progress',
                        'client_message_id': str(client_message_id),
                    }, status=status.HTTP_409_CONFLICT)

                quota_denied = _enforce_send_quota(account)
                if quota_denied is not None:
                    return quota_denied

                outbox = OutboxMessage.objects.create(
                    account=account,
                    client_message_id=client_message_id,
                    status='queued',
                    provider_started=False,
                    to_addresses=serializer.validated_data['to_addresses'],
                    cc_addresses=serializer.validated_data.get('cc_addresses', []),
                    bcc_addresses=serializer.validated_data.get('bcc_addresses', []),
                    subject=serializer.validated_data['subject'],
                    body_text=serializer.validated_data.get('body_text', ''),
                    body_html=serializer.validated_data.get('body_html', ''),
                )
        except IntegrityError:
            return Response({
                'status': 'conflict',
                'error': 'A send request with this client_message_id is already in progress',
                'client_message_id': str(client_message_id),
            }, status=status.HTTP_409_CONFLICT)

        try:
            mail_service = get_mail_service(request)

            # Process attachments (base64-encoded from frontend)
            attachments = []
            for att in serializer.validated_data.get('attachments', []):
                if isinstance(att, dict) and att.get('content'):
                    content_data = att['content']
                    # Strip data URI prefix if present (e.g. "data:image/png;base64,...")
                    if ',' in content_data:
                        content_data = content_data.split(',', 1)[1]
                    attachments.append({
                        'filename': att.get('filename', 'attachment'),
                        'content': base64.b64decode(content_data),
                        'content_type': att.get('content_type', 'application/octet-stream'),
                    })

            # Mark as sending
            outbox.status = 'sending'
            outbox.attachments = serializer.validated_data.get('attachments', [])
            outbox.save(update_fields=['status', 'attachments', 'updated_at'])

            # The provider boundary is durable and typed; never infer retry
            # safety from an exception string after this point.
            outbox.provider_started = True
            outbox.save(update_fields=['provider_started', 'updated_at'])

            # MAIL-53 canonical contract: a JMAP account sends through JMAP even
            # when SMTP settings are configured, because JMAP can make the
            # response and Sent-folder state agree atomically (Drafts -> Sent
            # via onSuccessUpdateEmail). SMTP remains the transport for
            # non-JMAP accounts with an outgoing server.
            if account.incoming_server_type == 'jmap':
                result = mail_service.send_email_jmap(
                    account=account,
                    to_addresses=serializer.validated_data['to_addresses'],
                    subject=serializer.validated_data['subject'],
                    body_text=serializer.validated_data.get('body_text', ''),
                    body_html=serializer.validated_data.get('body_html', ''),
                    cc_addresses=serializer.validated_data.get('cc_addresses', []),
                    bcc_addresses=serializer.validated_data.get('bcc_addresses', []),
                    in_reply_to=serializer.validated_data.get('in_reply_to'),
                    references=serializer.validated_data.get('references'),
                )
            elif account.outgoing_host:
                result = mail_service.send_email(
                    account=account,
                    to_addresses=serializer.validated_data['to_addresses'],
                    subject=serializer.validated_data['subject'],
                    body_text=serializer.validated_data.get('body_text', ''),
                    body_html=serializer.validated_data.get('body_html', ''),
                    cc_addresses=serializer.validated_data.get('cc_addresses', []),
                    bcc_addresses=serializer.validated_data.get('bcc_addresses', []),
                    reply_to=serializer.validated_data.get('reply_to'),
                    in_reply_to=serializer.validated_data.get('in_reply_to'),
                    references=serializer.validated_data.get('references'),
                    attachments=attachments if attachments else None,
                )
            else:
                raise MailServiceError('No outgoing SMTP server configured for account')

            # ── Success: update outbox ───────────────────────────────────
            outbox.status = 'sent'
            outbox.result = result
            outbox.sent_at = timezone.now()
            outbox.save(update_fields=['status', 'result', 'sent_at', 'updated_at'])

            # Delete draft if this was a draft send
            draft_id = serializer.validated_data.get('draft_id')
            if draft_id:
                MailDraft.objects.filter(id=draft_id, account=account).delete()

            logger.info("Email sent from %s to %s", redact_sensitive(account.email_address), redact_sensitive(serializer.validated_data['to_addresses']))

            return Response({
                'status': 'sent',
                'from': account.email_address,
                'to': serializer.validated_data['to_addresses'],
                'subject': serializer.validated_data['subject'],
                'message': 'Email sent successfully',
                'email_id': result.get('email_id') or result.get('message_id'),
                'client_message_id': str(client_message_id),
            })

        except MailServiceError as e:
            logger.error("Failed to send email: %s", safe_exception_text(e))

            # ── Failure: update outbox ───────────────────────────────────
            outbox.status = 'failed'
            outbox.error = safe_exception_text(e)
            outbox.save(update_fields=['status', 'error', 'updated_at'])

            if account.incoming_server_type == 'jmap':
                error = (
                    "Could not submit message via JMAP; response and Sent folder state "
                    f"were kept consistent: {safe_exception_text(e)}"
                )
            elif account.outgoing_host:
                error = (
                    "Could not send email via configured outgoing mail server; "
                    "re-test account connection and credentials"
                )
            else:
                error = "Could not send email; no outgoing mail transport is configured"
            return Response({
                'status': 'error',
                'error': public_provider_error(error),
                'client_message_id': str(client_message_id),
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class MailFilterViewSet(viewsets.ModelViewSet):
    """
    Mail filter (rule) management.
    """
    serializer_class = MailFilterSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        account_id = self.request.query_params.get('account_id')
        if account_id:
            account = get_account_by_id(self.request.user, account_id)
        else:
            account = get_default_account(self.request.user)

        if not account:
            return MailFilter.objects.none()

        return MailFilter.objects.filter(account=account)

    def create(self, request):
        """Create a new mail filter."""
        serializer = MailFilterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        account_id = request.data.get('account_id')
        account, error_response = resolve_account_for_request(request.user, account_id)
        if error_response:
            return error_response

        mail_filter = serializer.save(account=account)

        return Response(
            MailFilterSerializer(mail_filter).data,
            status=status.HTTP_201_CREATED
        )

    @action(detail=False, methods=['post'])
    def preview(self, request):
        """MAIL-64 dry-run: what the active filters WOULD do — NO mutation.

        Body: {"account_id"?: str, "messages": [<normalized message dict>, ...]}.
        The caller passes the message set to evaluate (e.g. the inbox currently
        loaded in the client), so preview is a pure function of the account's
        active rules and never touches Stalwart. Uses the same resolver as apply,
        so the preview matches what applying the filters would produce.
        Returns {"count": N, "preview": [<per-message planned actions>]}.
        """
        from .services.filter_engine import preview_filters_for_messages

        account_id = request.data.get('account_id')
        account, error_response = resolve_account_for_request(request.user, account_id)
        if error_response:
            return error_response

        messages = request.data.get('messages')
        if not isinstance(messages, list):
            return Response(
                {'error': 'messages must be a list of message objects'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        preview = preview_filters_for_messages(account, messages)
        return Response({'count': len(preview), 'preview': preview})


class MailContactViewSet(viewsets.ModelViewSet):
    """
    Contact/address book management.
    """
    serializer_class = MailContactSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        # Contacts are shared across all accounts for a user
        accounts = get_user_accounts(self.request.user)
        queryset = MailContact.objects.filter(account__in=accounts)

        search = self.request.query_params.get('search')
        if search:
            queryset = queryset.filter(
                Q(email__icontains=search) | Q(name__icontains=search)
            )

        # MAIL-163: rank by recency/frequency so the most relevant contacts
        # surface first (recently contacted, then most-used, then by name).
        return queryset.distinct().order_by(
            '-last_contacted_at', '-send_count', '-receive_count', 'name'
        )

    def create(self, request):
        """Add a new contact (dedup by account+email)."""
        serializer = MailContactSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        account = get_default_account(request.user)
        if not account:
            return Response({'error': 'No accounts configured'}, status=404)

        email = serializer.validated_data.get('email', '').strip().lower()
        existing = MailContact.objects.filter(account=account, email=email).first()
        if existing:
            # MAIL-163: deterministic merge — update name, keep usage signals.
            name = serializer.validated_data.get('name', '')
            if name:
                existing.name = name
            existing.save(update_fields=['name', 'updated_at'])
            return Response(
                MailContactSerializer(existing).data,
                status=status.HTTP_200_OK,
            )

        # Normalize the stored email so dedup is case-insensitive.
        serializer.validated_data['email'] = email
        contact = serializer.save(account=account, is_manual=True)

        return Response(
            MailContactSerializer(contact).data,
            status=status.HTTP_201_CREATED
        )

    @action(detail=False, methods=['get'])
    def autocomplete(self, request):
        """Autocomplete for email addresses (ranked by recency/frequency)."""
        query = request.query_params.get('q', '')
        if len(query) < 2:
            return Response([])

        accounts = get_user_accounts(request.user)
        contacts = MailContact.objects.filter(
            account__in=accounts
        ).filter(
            Q(email__icontains=query) | Q(name__icontains=query)
        ).distinct().order_by(
            '-last_contacted_at', '-send_count', '-receive_count', 'name'
        )[:10]

        serializer = MailContactSerializer(contacts, many=True)
        return Response(serializer.data)


class InternalDeliveryEventView(APIView):
    """Apply monotonic provider DSN/bounce evidence without re-sending."""

    permission_classes = [AllowAny]
    _rank = {'pending': 0, 'delayed': 1, 'delivered': 2, 'failed': 3}

    def post(self, request):
        if not request.headers.get('X-Internal-Service'):
            return Response({'code': 'missing_internal_service'}, status=status.HTTP_401_UNAUTHORIZED)
        try:
            account_id = uuid.UUID(str(request.data.get('account_id', '')))
        except ValueError:
            return Response({'code': 'invalid_account_id'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            client_message_id = uuid.UUID(str(request.data.get('client_message_id', '')))
        except ValueError:
            return Response({'code': 'invalid_client_message_id'}, status=status.HTTP_400_BAD_REQUEST)
        delivery_status = request.data.get('delivery_status')
        if delivery_status not in self._rank:
            return Response({'code': 'invalid_delivery_status'}, status=status.HTTP_400_BAD_REQUEST)
        with transaction.atomic():
            outbox = OutboxMessage.objects.select_for_update().filter(
                account_id=account_id, client_message_id=client_message_id,
            ).first()
            if outbox is None:
                raise NotFound('No send record matches the client_message_id.')
            # A duplicate or weaker/out-of-order event is an idempotent no-op.
            if self._rank[delivery_status] > self._rank[outbox.delivery_status]:
                outbox.delivery_status = delivery_status
                outbox.delivery_diagnostic = str(request.data.get('diagnostic', ''))[:4000]
                outbox.delivery_updated_at = timezone.now()
                outbox.save(update_fields=[
                    'delivery_status', 'delivery_diagnostic',
                    'delivery_updated_at', 'updated_at',
                ])
        return Response({
            'client_message_id': str(outbox.client_message_id),
            'delivery_status': outbox.delivery_status,
            'delivery_diagnostic': outbox.delivery_diagnostic,
        })


class InternalSendView(APIView):
    """
    Internal endpoint for system emails (password reset, notifications, etc.)

    Only accepts requests from internal services (verified by X-Internal-Service header).
    Uses the configured system email account instead of auto-provisioning.
    """
    permission_classes = [AllowAny]  # Protected by header check

    def post(self, request):
        # Verify internal service header
        internal_header = request.META.get('HTTP_X_INTERNAL_SERVICE')
        if not internal_header:
            logger.warning("InternalSendView called without X-Internal-Service header")
            return Response({'error': 'Unauthorized'}, status=status.HTTP_403_FORBIDDEN)

        serializer = InternalSendSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        from_email = serializer.validated_data.get('from_email')
        to_addresses = serializer.validated_data['to_addresses']
        subject = serializer.validated_data['subject']
        body_text = serializer.validated_data.get('body_text', '')
        body_html = serializer.validated_data.get('body_html', '')

        # Resolve the sending account once
        system_email = getattr(settings, 'SYSTEM_EMAIL_ADDRESS', 'noreply@shizuha.com')
        system_user_id = getattr(settings, 'SYSTEM_USER_ID', 1)

        if from_email:
            account = MailAccount.objects.filter(
                email_address=from_email,
                is_active=True
            ).first()
        else:
            account = MailAccount.objects.filter(
                email_address=system_email,
                user_id=system_user_id,
                is_active=True
            ).first()

        if not account:
            logger.error("System email account not found: %s", redact_sensitive(system_email))
            return Response(
                {'error': 'System email account not configured. Run: python manage.py create_system_account'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

        # ── Idempotency check (account-scoped) ────────────────────────────
        client_message_id = serializer.validated_data.get('client_message_id')
        if client_message_id is not None:
            existing = OutboxMessage.objects.filter(
                account=account,
                client_message_id=client_message_id
            ).first()
            if existing is not None:
                if existing.status == 'sent' and existing.result:
                    return Response(existing.result)
                elif existing.status == 'failed':
                    return Response(
                        {'error': existing.error or 'Send failed'},
                        status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    )
                else:
                    return Response(
                        {'error': 'A send request with this client_message_id is already in progress'},
                        status=status.HTTP_409_CONFLICT,
                    )

        try:
            from .services.mail_service import get_mail_service, MailServiceError

            # Create outbox entry if client_message_id was provided
            outbox = None
            if client_message_id is not None:
                try:
                    outbox = OutboxMessage.objects.create(
                        account=account,
                        client_message_id=client_message_id,
                        status='queued',
                        to_addresses=to_addresses,
                        subject=subject,
                        body_text=body_text,
                        body_html=body_html,
                    )
                except IntegrityError:
                    return Response(
                        {'error': 'A send request with this client_message_id is already in progress'},
                        status=status.HTTP_409_CONFLICT,
                    )
                outbox.status = 'sending'
                outbox.save(update_fields=['status', 'updated_at'])

            mail_service = get_mail_service(request)
            result = mail_service.send_email(
                account=account,
                to_addresses=to_addresses,
                subject=subject,
                body_text=body_text,
                body_html=body_html,
            )

            # Update outbox on success
            if outbox is not None:
                outbox.status = 'sent'
                outbox.result = result
                outbox.sent_at = timezone.now()
                outbox.save(update_fields=['status', 'result', 'sent_at', 'updated_at'])

            logger.info("Internal email sent via %s to %s by %s", redact_sensitive(account.email_address), redact_sensitive(to_addresses), redact_sensitive(internal_header))
            return Response(result)

        except Exception as e:
            logger.error("Internal send failed: %s", safe_exception_text(e))

            # Update outbox on failure
            if outbox is not None:
                outbox.status = 'failed'
                outbox.error = safe_exception_text(e)
                outbox.save(update_fields=['status', 'error', 'updated_at'])

            return Response(
                {'error': public_provider_error('Could not send internal email')},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
