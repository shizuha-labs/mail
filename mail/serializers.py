"""
Serializers for Shizuha Mail API.
"""
import base64
import binascii

from django.conf import settings
from rest_framework import serializers
from .models import (
    MailAccount, MailFolder, MailThread, MailMessage,
    MailAttachment, MailDraft, MailFilter, MailContact
)


class MailAccountSerializer(serializers.ModelSerializer):
    """Serializer for mail account details (read-only for list/detail views)."""
    unread_count = serializers.SerializerMethodField()

    class Meta:
        model = MailAccount
        fields = [
            'id', 'user_id', 'display_name', 'from_name', 'email_address',
            'incoming_server_type', 'incoming_host', 'incoming_port', 'incoming_security',
            'incoming_username', 'auth_type',
            'outgoing_host', 'outgoing_port', 'outgoing_security',
            'outgoing_username', 'outgoing_requires_auth',
            'settings', 'signature',
            'is_active', 'is_default', 'connection_status', 'connection_error',
            'last_connection_check', 'last_sync_at',
            'created_at', 'updated_at', 'unread_count'
        ]
        read_only_fields = [
            'id', 'user_id', 'connection_status', 'connection_error',
            'last_connection_check', 'last_sync_at',
            'created_at', 'updated_at'
        ]

    def get_unread_count(self, obj):
        return obj.messages.filter(is_unread=True).count()


class MailAccountCreateSerializer(serializers.Serializer):
    """Serializer for creating a new mail account."""
    display_name = serializers.CharField(max_length=100, required=False, allow_blank=True)
    from_name = serializers.CharField(max_length=255, required=False, allow_blank=True)
    email_address = serializers.EmailField()

    # Incoming server config
    incoming_server_type = serializers.ChoiceField(
        choices=['imap', 'jmap'],
        default='imap'
    )
    incoming_host = serializers.CharField(max_length=255)
    incoming_port = serializers.IntegerField(default=993)
    incoming_security = serializers.ChoiceField(
        choices=['ssl', 'starttls', 'none'],
        default='ssl'
    )
    incoming_username = serializers.CharField(max_length=255)
    incoming_password = serializers.CharField(max_length=255, write_only=True)

    # Outgoing server config
    outgoing_host = serializers.CharField(max_length=255)
    outgoing_port = serializers.IntegerField(default=587)
    outgoing_security = serializers.ChoiceField(
        choices=['ssl', 'starttls', 'none'],
        default='starttls'
    )
    outgoing_username = serializers.CharField(max_length=255, required=False, allow_blank=True)
    outgoing_password = serializers.CharField(max_length=255, required=False, allow_blank=True, write_only=True)
    outgoing_requires_auth = serializers.BooleanField(default=True)

    # Optional settings
    signature = serializers.CharField(required=False, allow_blank=True)
    is_default = serializers.BooleanField(default=False)


class MailAccountUpdateSerializer(serializers.Serializer):
    """Serializer for updating mail account settings."""
    display_name = serializers.CharField(max_length=100, required=False, allow_blank=True)
    from_name = serializers.CharField(max_length=255, required=False, allow_blank=True)
    signature = serializers.CharField(required=False, allow_blank=True)
    settings = serializers.JSONField(required=False)
    is_default = serializers.BooleanField(required=False)

    # Allow updating passwords (but not reading)
    incoming_password = serializers.CharField(max_length=255, required=False, write_only=True)
    outgoing_password = serializers.CharField(max_length=255, required=False, allow_blank=True, write_only=True)


class EmailProviderPresetSerializer(serializers.Serializer):
    """Serializer for email provider presets."""
    id = serializers.CharField()
    name = serializers.CharField()
    domains = serializers.ListField(child=serializers.CharField())
    incoming_host = serializers.CharField()
    incoming_port = serializers.IntegerField()
    incoming_security = serializers.CharField()
    incoming_server_type = serializers.CharField()
    outgoing_host = serializers.CharField()
    outgoing_port = serializers.IntegerField()
    outgoing_security = serializers.CharField()
    notes = serializers.CharField(required=False, allow_blank=True)


class MailFolderSerializer(serializers.ModelSerializer):
    """Serializer for mail folders."""
    children = serializers.SerializerMethodField()

    class Meta:
        model = MailFolder
        fields = [
            'id', 'name', 'role', 'parent', 'sort_order',
            'total_messages', 'unread_messages', 'total_size_bytes',
            'created_at', 'updated_at', 'children'
        ]
        read_only_fields = ['id', 'jmap_mailbox_id', 'total_messages', 'unread_messages', 'total_size_bytes']

    def get_children(self, obj):
        children = obj.children.all()
        return MailFolderSerializer(children, many=True).data if children.exists() else []


class MailFolderCreateSerializer(serializers.Serializer):
    """Serializer for creating a new folder."""
    name = serializers.CharField(max_length=255)
    parent_id = serializers.UUIDField(required=False, allow_null=True)


class MailThreadSerializer(serializers.ModelSerializer):
    """Serializer for mail threads."""
    messages = serializers.SerializerMethodField()

    class Meta:
        model = MailThread
        fields = [
            'id', 'subject', 'participants', 'message_count', 'unread_count',
            'first_message_at', 'last_message_at', 'messages'
        ]

    def get_messages(self, obj):
        # Only include basic message info in thread view
        messages = obj.messages.all()[:20]  # Limit to 20 messages
        return MailMessageListSerializer(messages, many=True).data


class MailAttachmentSerializer(serializers.ModelSerializer):
    """Serializer for message attachments."""

    class Meta:
        model = MailAttachment
        fields = [
            'id', 'filename', 'content_type', 'size_bytes',
            'is_inline', 'content_id'
        ]


class MailMessageListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for message list views."""
    folder_name = serializers.CharField(source='folder.name', read_only=True)
    account_id = serializers.UUIDField(source='account.id', read_only=True)

    class Meta:
        model = MailMessage
        fields = [
            'id', 'account_id', 'subject', 'from_address', 'from_name',
            'to_addresses', 'preview', 'is_unread', 'is_flagged',
            'is_answered', 'has_attachments', 'attachment_count',
            'date_sent', 'date_received', 'folder_name', 'thread_id'
        ]


class MailMessageDetailSerializer(serializers.ModelSerializer):
    """Full serializer for message detail views."""
    attachments = MailAttachmentSerializer(many=True, read_only=True)
    folder_name = serializers.CharField(source='folder.name', read_only=True)
    account_id = serializers.UUIDField(source='account.id', read_only=True)

    class Meta:
        model = MailMessage
        fields = [
            'id', 'account_id', 'jmap_email_id', 'message_id', 'in_reply_to', 'references',
            'subject', 'from_address', 'from_name',
            'to_addresses', 'cc_addresses', 'bcc_addresses', 'reply_to',
            'preview', 'is_unread', 'is_flagged', 'is_answered', 'is_forwarded', 'is_draft',
            'has_attachments', 'attachment_count', 'attachments',
            'size_bytes', 'labels',
            'date_sent', 'date_received', 'folder', 'folder_name', 'thread_id',
            'created_at', 'updated_at'
        ]


class MailMessageMoveSerializer(serializers.Serializer):
    """Serializer for moving messages."""
    message_ids = serializers.ListField(child=serializers.CharField())  # JMAP IDs are strings
    account_id = serializers.UUIDField(required=False, allow_null=True)  # For multi-account support
    folder_id = serializers.UUIDField(required=False, allow_null=True)
    folder_role = serializers.ChoiceField(
        choices=['inbox', 'drafts', 'sent', 'trash', 'junk', 'archive'],
        required=False,
        allow_null=True
    )
    from_folder = serializers.CharField(required=False, allow_blank=True)

    def validate(self, data):
        if not data.get('folder_id') and not data.get('folder_role'):
            raise serializers.ValidationError("Either folder_id or folder_role is required")
        return data


class MailMessageFlagSerializer(serializers.Serializer):
    """Serializer for updating message flags."""
    message_ids = serializers.ListField(child=serializers.CharField())  # JMAP IDs are strings
    account_id = serializers.UUIDField(required=False, allow_null=True)  # For multi-account support
    folder_id = serializers.CharField(required=False, allow_blank=True)  # Required for IMAP
    is_unread = serializers.BooleanField(required=False)
    is_flagged = serializers.BooleanField(required=False)


class MailDraftSerializer(serializers.ModelSerializer):
    """Serializer for email drafts."""
    base_revision = serializers.IntegerField(write_only=True, required=False, min_value=1)

    class Meta:
        model = MailDraft
        fields = [
            'id', 'reply_to_message', 'is_reply', 'is_reply_all', 'is_forward',
            'to_addresses', 'cc_addresses', 'bcc_addresses', 'subject',
            'body_text', 'body_html', 'attachments',
            'revision', 'base_revision',
            'is_synced', 'last_synced_at',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'revision', 'is_synced', 'last_synced_at', 'created_at', 'updated_at']

    def create(self, validated_data):
        validated_data.pop('base_revision', None)
        return super().create(validated_data)


class MailSendSerializer(serializers.Serializer):
    """Serializer for sending emails."""
    account_id = serializers.UUIDField(required=False, allow_null=True)  # Uses default account if not specified
    to_addresses = serializers.ListField(
        child=serializers.EmailField(), required=False, default=[],
    )
    cc_addresses = serializers.ListField(child=serializers.EmailField(), required=False, default=[])
    bcc_addresses = serializers.ListField(child=serializers.EmailField(), required=False, default=[])
    subject = serializers.CharField(max_length=1000, allow_blank=True)
    body_text = serializers.CharField(allow_blank=True, required=False)
    body_html = serializers.CharField(allow_blank=True, required=False)
    # MAIL-164: accept the common email-API field names `body`/`html` as
    # write-only aliases for `body_text`/`body_html`. DRF silently drops
    # undeclared fields, so without these a client sending `body` got a 200
    # "sent" with an empty-body message. They are mapped onto the canonical
    # fields in validate() so the outbox persists the content.
    body = serializers.CharField(allow_blank=True, required=False, write_only=True)
    html = serializers.CharField(allow_blank=True, required=False, write_only=True)
    reply_to = serializers.EmailField(required=False, allow_null=True)
    in_reply_to = serializers.CharField(required=False, allow_blank=True)
    references = serializers.ListField(child=serializers.CharField(), required=False, default=[])
    # Provider message identifiers are opaque protocol keys (for example a
    # compact JMAP Email id), not necessarily local MailMessage UUIDs.
    reply_to_message_id = serializers.CharField(
        required=False,
        allow_null=True,
        allow_blank=False,
        max_length=500,
    )
    # IMAP UIDs are mailbox-scoped, unlike JMAP Email ids. The browser may
    # carry this opaque folder reference from the message it opened.
    reply_to_folder_id = serializers.CharField(
        required=False,
        allow_null=True,
        allow_blank=False,
        max_length=500,
    )
    draft_id = serializers.UUIDField(required=False, allow_null=True)
    attachments = serializers.ListField(required=False, default=[])
    client_message_id = serializers.UUIDField(required=False, help_text="Client-generated idempotency key")

    def validate(self, attrs):
        """Reject decoded attachment/message oversize and recipient amplification
        before provider execution."""
        # MAIL-164: map the common `body`/`html` aliases onto the canonical
        # fields. A canonical field explicitly provided by the client wins;
        # otherwise the alias is persisted so content is never silently dropped.
        if not attrs.get('body_text') and attrs.get('body'):
            attrs['body_text'] = attrs.pop('body')
        if not attrs.get('body_html') and attrs.get('html'):
            attrs['body_html'] = attrs.pop('html')
        attrs.pop('body', None)
        attrs.pop('html', None)
        if not attrs.get('to_addresses') and not attrs.get('reply_to_message_id'):
            raise serializers.ValidationError({
                'to_addresses': 'At least one recipient or a reply source is required.',
            })
        # MAIL-157: bound total envelope recipients so one admitted request can
        # never fan out past the configured cap (defense-in-depth behind the
        # v1 one-recipient agent surface). Rejected here → zero Outbox/provider
        # work.
        max_recipients = getattr(settings, 'MAIL_MAX_RECIPIENTS_PER_SEND', 50)
        envelope = (
            len(attrs.get('to_addresses', []))
            + len(attrs.get('cc_addresses', []))
            + len(attrs.get('bcc_addresses', []))
        )
        if envelope > max_recipients:
            raise serializers.ValidationError({
                'to_addresses': f'At most {max_recipients} total recipients allowed per send.',
            })
        attachment_limit = getattr(settings, 'MAX_UPLOAD_SIZE', 26214400)
        message_limit = getattr(settings, 'MAX_MESSAGE_SIZE', attachment_limit)
        total = len(attrs.get('body_text', '').encode()) + len(attrs.get('body_html', '').encode())
        for index, attachment in enumerate(attrs.get('attachments', [])):
            if not isinstance(attachment, dict) or not isinstance(attachment.get('content'), str):
                raise serializers.ValidationError({
                    'error_type': 'invalid_attachment', 'attachment_index': index,
                })
            content = attachment['content']
            encoded = content.split(',', 1)[1] if content.startswith('data:') and ',' in content else content
            try:
                size = len(base64.b64decode(encoded, validate=True))
            except (binascii.Error, ValueError):
                raise serializers.ValidationError({
                    'error_type': 'invalid_base64', 'attachment_index': index,
                })
            if size > attachment_limit:
                raise serializers.ValidationError({
                    'error_type': 'attachment_too_large', 'attachment_index': index,
                    'max_decoded_bytes': attachment_limit,
                })
            total += size
        if total > message_limit:
            raise serializers.ValidationError({
                'error_type': 'message_too_large', 'max_decoded_bytes': message_limit,
            })
        return attrs


class MailFilterSerializer(serializers.ModelSerializer):
    """Serializer for mail filters."""

    class Meta:
        model = MailFilter
        fields = [
            'id', 'name', 'description', 'priority',
            'match_type', 'conditions', 'actions',
            'stop_processing', 'is_active', 'is_synced',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'sieve_script', 'is_synced', 'created_at', 'updated_at']


class MailContactSerializer(serializers.ModelSerializer):
    """Serializer for mail contacts."""

    class Meta:
        model = MailContact
        fields = [
            'id', 'email', 'name', 'send_count', 'receive_count',
            'last_contacted_at', 'is_manual', 'created_at'
        ]
        read_only_fields = ['id', 'send_count', 'receive_count', 'last_contacted_at', 'created_at']


class MailSearchSerializer(serializers.Serializer):
    """Serializer for local metadata search queries.

    Accepts the original API shape (`query`, `account_id`, `folder_id`) plus
    MCP/frontend aliases (`q`, `account`, `folder`, `has_attachments`,
    `is_read`). Cross-account search is opt-in via `scope=all|unified` or an
    explicit `account_ids` list, preserving the legacy default-account behavior.
    """
    query = serializers.CharField(max_length=500, required=False, allow_blank=False)
    q = serializers.CharField(max_length=500, required=False, allow_blank=False, write_only=True)
    account_id = serializers.UUIDField(required=False, allow_null=True)
    account = serializers.UUIDField(required=False, allow_null=True, write_only=True)
    account_ids = serializers.ListField(child=serializers.UUIDField(), required=False, allow_empty=False)
    scope = serializers.ChoiceField(choices=['account', 'all', 'unified'], required=False, default='account')
    folder_id = serializers.UUIDField(required=False, allow_null=True)
    folder = serializers.CharField(max_length=255, required=False, allow_blank=False, allow_null=True)
    from_address = serializers.EmailField(required=False, allow_null=True)
    to_address = serializers.EmailField(required=False, allow_null=True)
    has_attachment = serializers.BooleanField(required=False, allow_null=True)
    has_attachments = serializers.BooleanField(required=False, allow_null=True, write_only=True)
    is_unread = serializers.BooleanField(required=False, allow_null=True)
    is_read = serializers.BooleanField(required=False, allow_null=True, write_only=True)
    is_flagged = serializers.BooleanField(required=False, allow_null=True)
    date_from = serializers.DateTimeField(required=False, allow_null=True)
    date_to = serializers.DateTimeField(required=False, allow_null=True)
    page_size = serializers.IntegerField(required=False, min_value=1, max_value=100, default=20)
    cursor = serializers.CharField(required=False, allow_blank=False)
    # MAIL-46 (Slice 5): scoped relevance-search extensions. All additive;
    # legacy single `account`/`folder` params keep working unchanged.
    folders = serializers.ListField(
        child=serializers.CharField(max_length=255), required=False, allow_empty=False)
    thread_id = serializers.UUIDField(required=False, allow_null=True)
    sort = serializers.ChoiceField(
        choices=['relevance', 'date_desc', 'date_asc'], required=False, default='relevance')
    include_snippets = serializers.BooleanField(required=False, default=True)

    def validate(self, attrs):
        attrs['query'] = attrs.get('query') or attrs.get('q')
        if not attrs.get('query'):
            raise serializers.ValidationError({'query': 'This field is required.'})
        if attrs.get('account') and not attrs.get('account_id'):
            attrs['account_id'] = attrs['account']
        if attrs.get('has_attachments') is not None and attrs.get('has_attachment') is None:
            attrs['has_attachment'] = attrs['has_attachments']
        if attrs.get('is_read') is not None and attrs.get('is_unread') is None:
            attrs['is_unread'] = not attrs['is_read']
        return attrs


class ConnectionTestResultSerializer(serializers.Serializer):
    """Serializer for connection test results."""
    incoming = serializers.DictField()
    outgoing = serializers.DictField()


class InternalSendSerializer(serializers.Serializer):
    """Serializer for internal send endpoint."""
    from_email = serializers.EmailField(required=False)  # Optional, uses system account if not provided
    to_addresses = serializers.ListField(child=serializers.EmailField())
    subject = serializers.CharField(max_length=1000)
    body_text = serializers.CharField(allow_blank=True, required=False)
    body_html = serializers.CharField(allow_blank=True, required=False)
    client_message_id = serializers.UUIDField(required=False, help_text="Client-generated idempotency key")
