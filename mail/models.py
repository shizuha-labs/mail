"""
Models for Shizuha Mail - Generic email client supporting any IMAP/SMTP/JMAP provider.

This service acts as a multi-account email client (like Outlook/Thunderbird):
- Users manually add email accounts with their own credentials
- Supports multiple accounts per user
- Supports any IMAP/SMTP/JMAP provider (Gmail, Outlook, Yahoo, Stalwart, etc.)
- Credentials are encrypted at rest
"""
import uuid
from django.db import models
from django.contrib.postgres.search import SearchVectorField
from django.db.models import Q
from django.utils import timezone
from django.conf import settings


class MailAccount(models.Model):
    """
    User's email account configuration.
    Users can add multiple accounts from any email provider.
    """

    # Server type choices
    SERVER_TYPE_CHOICES = [
        ('imap', 'IMAP'),
        ('jmap', 'JMAP'),
    ]

    # Security type choices
    SECURITY_CHOICES = [
        ('ssl', 'SSL/TLS'),
        ('starttls', 'STARTTLS'),
        ('none', 'None'),
    ]

    # Connection status choices
    CONNECTION_STATUS_CHOICES = [
        ('unknown', 'Unknown'),
        ('connected', 'Connected'),
        ('auth_failed', 'Authentication Failed'),
        ('needs_reauth', 'Re-authorization Required'),
        ('connection_failed', 'Connection Failed'),
        ('ssl_error', 'SSL/TLS Error'),
        ('needs_attention', 'Needs Attention'),
    ]

    # Authentication type choices
    AUTH_TYPE_CHOICES = [
        ('password', 'Password'),
        ('oauth2', 'OAuth 2.0'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # Link to Shizuha ID user (multiple accounts per user allowed)
    user_id = models.IntegerField(db_index=True, help_text="User ID from Shizuha ID")

    # Account display settings
    display_name = models.CharField(max_length=100, blank=True, help_text="Account label (e.g., 'Work', 'Personal')")
    from_name = models.CharField(max_length=255, blank=True, help_text="Display name for From header")

    # Email address for this account
    email_address = models.EmailField(help_text="Email address for this account")

    # Incoming server configuration
    incoming_server_type = models.CharField(
        max_length=10,
        choices=SERVER_TYPE_CHOICES,
        default='imap',
        help_text="Protocol for receiving mail"
    )
    incoming_host = models.CharField(max_length=255, help_text="Incoming mail server hostname")
    incoming_port = models.IntegerField(default=993, help_text="Incoming mail server port")
    incoming_security = models.CharField(
        max_length=10,
        choices=SECURITY_CHOICES,
        default='ssl',
        help_text="Connection security for incoming"
    )
    incoming_username = models.CharField(max_length=255, help_text="Username for incoming server (usually email)")
    encrypted_incoming_password = models.BinaryField(
        blank=True,
        null=True,
        help_text="Encrypted password for incoming server"
    )
    auth_type = models.CharField(
        max_length=10,
        choices=AUTH_TYPE_CHOICES,
        default='password',
        help_text="Authentication mechanism: password or OAuth 2.0 token",
    )

    # OAuth provider (only set when auth_type='oauth2')
    OAUTH_PROVIDER_CHOICES = [
        ('gmail', 'Gmail'),
        ('outlook', 'Outlook / Microsoft 365'),
    ]
    oauth_provider = models.CharField(
        max_length=20,
        choices=OAUTH_PROVIDER_CHOICES,
        blank=True,
        default='',
        help_text="OAuth provider name (gmail or outlook); empty for password accounts",
    )
    encrypted_oauth_access_token = models.BinaryField(
        blank=True,
        null=True,
        help_text="Encrypted OAuth access token for XOAUTH2 IMAP/SMTP auth",
    )
    encrypted_oauth_refresh_token = models.BinaryField(
        blank=True,
        null=True,
        help_text="Encrypted OAuth refresh token for token renewal",
    )
    oauth_token_expiry = models.DateTimeField(
        null=True,
        blank=True,
        help_text="UTC expiry of the current OAuth access token",
    )

    # Outgoing server configuration (SMTP)
    outgoing_host = models.CharField(max_length=255, help_text="SMTP server hostname")
    outgoing_port = models.IntegerField(default=587, help_text="SMTP server port")
    outgoing_security = models.CharField(
        max_length=10,
        choices=SECURITY_CHOICES,
        default='starttls',
        help_text="Connection security for outgoing"
    )
    outgoing_username = models.CharField(max_length=255, blank=True, help_text="Username for SMTP (defaults to incoming)")
    encrypted_outgoing_password = models.BinaryField(
        blank=True,
        null=True,
        help_text="Encrypted password for SMTP (if different from incoming)"
    )
    outgoing_requires_auth = models.BooleanField(default=True, help_text="Whether SMTP requires authentication")

    # Account settings (JSON for user preferences)
    settings = models.JSONField(default=dict, blank=True, help_text="User mail preferences")
    signature = models.TextField(blank=True, help_text="Email signature")

    # Status
    is_active = models.BooleanField(default=True)
    is_default = models.BooleanField(default=False, help_text="Default account for sending")
    connection_status = models.CharField(
        max_length=20,
        choices=CONNECTION_STATUS_CHOICES,
        default='unknown',
        help_text="Last known connection status"
    )
    connection_error = models.TextField(blank=True, help_text="Last connection error message")
    last_connection_check = models.DateTimeField(null=True, blank=True)

    # Sync tracking
    last_sync_at = models.DateTimeField(null=True, blank=True)
    sync_state = models.CharField(max_length=255, blank=True, help_text="JMAP/IMAP sync state token")

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'mail_account'
        ordering = ['-is_default', '-created_at']
        unique_together = [['user_id', 'email_address']]

    def __str__(self):
        label = self.display_name or self.email_address
        return f"{label} (user {self.user_id})"

    def save(self, *args, **kwargs):
        # If this account is being set as default, unset other defaults for this user
        if self.is_default:
            MailAccount.objects.filter(
                user_id=self.user_id,
                is_default=True
            ).exclude(pk=self.pk).update(is_default=False)

        # If no display_name, use email address
        if not self.display_name:
            self.display_name = self.email_address

        # If outgoing username not set, use incoming username
        if not self.outgoing_username:
            self.outgoing_username = self.incoming_username

        super().save(*args, **kwargs)


class UserLifecycleEventReceipt(models.Model):
    """Idempotency ledger for signed Shizuha ID lifecycle events."""

    STATUS_PROCESSING = 'processing'
    STATUS_PROCESSED = 'processed'
    STATUS_FAILED = 'failed'
    STATUS_CHOICES = [
        (STATUS_PROCESSING, 'Processing'),
        (STATUS_PROCESSED, 'Processed'),
        (STATUS_FAILED, 'Failed'),
    ]

    event_id = models.UUIDField(unique=True)
    event_type = models.CharField(max_length=40)
    user_id = models.IntegerField(db_index=True)
    mailbox_email = models.EmailField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PROCESSING)
    attempts = models.PositiveIntegerField(default=0)
    last_error = models.TextField(blank=True, default='')
    processed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'mail_user_lifecycle_event_receipt'
        ordering = ['created_at']

    def __str__(self):
        return f'{self.event_id} {self.event_type} ({self.status})'


class MailFolder(models.Model):
    """
    Cached mailbox/folder metadata from Stalwart.
    Synced from JMAP Mailbox objects.
    """
    ROLE_CHOICES = [
        ('inbox', 'Inbox'),
        ('drafts', 'Drafts'),
        ('sent', 'Sent'),
        ('trash', 'Trash'),
        ('junk', 'Junk/Spam'),
        ('archive', 'Archive'),
        ('custom', 'Custom Folder'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    account = models.ForeignKey(MailAccount, on_delete=models.CASCADE, related_name='folders')

    # JMAP identifiers
    jmap_mailbox_id = models.CharField(max_length=255, db_index=True)

    # Folder info
    name = models.CharField(max_length=255)
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='custom')

    # Hierarchy
    parent = models.ForeignKey('self', null=True, blank=True, on_delete=models.CASCADE, related_name='children')
    sort_order = models.IntegerField(default=0)

    # Stats (cached from Stalwart)
    total_messages = models.IntegerField(default=0)
    unread_messages = models.IntegerField(default=0)
    total_size_bytes = models.BigIntegerField(default=0)

    # MAIL-7: IMAP UIDVALIDITY epoch for this folder. A change invalidates all
    # stored UIDs for the folder → trigger a clean re-map instead of UID-deduping
    # against stale rows.
    uidvalidity = models.BigIntegerField(null=True, blank=True)

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'mail_folder'
        unique_together = [['account', 'jmap_mailbox_id']]
        ordering = ['sort_order', 'name']

    def __str__(self):
        return f"{self.name} ({self.account.email_address})"


class MailThread(models.Model):
    """
    Email thread/conversation grouping.
    Maps to JMAP Thread objects.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    account = models.ForeignKey(MailAccount, on_delete=models.CASCADE, related_name='threads')

    # JMAP identifiers
    jmap_thread_id = models.CharField(max_length=255, db_index=True)

    # Thread summary
    subject = models.CharField(max_length=1000, blank=True)
    participants = models.JSONField(default=list, blank=True, help_text="List of email addresses in thread")

    # Stats
    message_count = models.IntegerField(default=0)
    unread_count = models.IntegerField(default=0)

    # Timestamps
    first_message_at = models.DateTimeField(null=True, blank=True)
    last_message_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'mail_thread'
        unique_together = [['account', 'jmap_thread_id']]
        ordering = ['-last_message_at']

    def __str__(self):
        return f"Thread: {self.subject[:50]}"


class MailMessage(models.Model):
    """
    Cached email message metadata.
    The actual message content is stored in Stalwart; this is a fast-lookup cache.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    account = models.ForeignKey(MailAccount, on_delete=models.CASCADE, related_name='messages')
    folder = models.ForeignKey(MailFolder, on_delete=models.CASCADE, related_name='messages')
    thread = models.ForeignKey(MailThread, null=True, blank=True, on_delete=models.SET_NULL, related_name='messages')

    # Protocol identifiers
    jmap_email_id = models.CharField(max_length=255, blank=True, db_index=True)
    jmap_blob_id = models.CharField(max_length=255, blank=True, help_text="Blob ID for raw message")
    imap_uid = models.CharField(max_length=50, blank=True, db_index=True, help_text="IMAP UID for incremental sync")
    # MAIL-7: the UIDVALIDITY epoch this imap_uid belongs to — (folder, uidvalidity,
    # imap_uid) is the stable identity; a UIDVALIDITY change re-namespaces UIDs.
    uidvalidity = models.BigIntegerField(null=True, blank=True)

    # Message envelope
    message_id = models.CharField(max_length=500, blank=True, help_text="Message-ID header")
    in_reply_to = models.CharField(max_length=500, blank=True)
    references = models.JSONField(default=list, blank=True)

    # Headers
    subject = models.CharField(max_length=1000, blank=True)
    from_address = models.EmailField(blank=True)
    from_name = models.CharField(max_length=255, blank=True)
    to_addresses = models.JSONField(default=list)
    cc_addresses = models.JSONField(default=list, blank=True)
    bcc_addresses = models.JSONField(default=list, blank=True)
    reply_to = models.EmailField(blank=True)

    # Preview
    preview = models.TextField(blank=True, help_text="First ~200 chars of message body")
    # MAIL-19: plain-text body cache for FTS at weight D. Source of truth is Stalwart.
    # First 256 KB stored; body_text_truncated=True when the body was capped.
    body_text = models.TextField(null=True, blank=True)
    body_text_truncated = models.BooleanField(default=False)
    search_vector = SearchVectorField(null=True, blank=True, editable=False)

    # Flags
    is_unread = models.BooleanField(default=True)
    is_flagged = models.BooleanField(default=False)
    is_answered = models.BooleanField(default=False)
    is_forwarded = models.BooleanField(default=False)
    is_draft = models.BooleanField(default=False)

    # Attachments summary
    has_attachments = models.BooleanField(default=False)
    attachment_count = models.IntegerField(default=0)

    # Size
    size_bytes = models.IntegerField(default=0)

    # Labels/Keywords (JMAP keywords)
    labels = models.JSONField(default=list, blank=True)

    # Timestamps
    date_sent = models.DateTimeField(null=True, blank=True)
    date_received = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'mail_message'
        # jmap_email_id unique for JMAP, imap_uid+folder for IMAP
        # Can't enforce both in unique_together since one will be blank
        ordering = ['-date_received']
        indexes = [
            models.Index(fields=['account', 'folder', '-date_received']),
            models.Index(fields=['account', 'is_unread']),
            models.Index(fields=['account', 'is_flagged']),
        ]
        constraints = [
            # MAIL-7: hard idempotency for IMAP messages — one row per
            # (folder, UIDVALIDITY epoch, UID). Partial (uidvalidity NOT NULL)
            # so JMAP rows and legacy pre-UIDVALIDITY rows are unaffected.
            models.UniqueConstraint(
                fields=['folder', 'uidvalidity', 'imap_uid'],
                condition=Q(uidvalidity__isnull=False) & ~Q(imap_uid=''),
                name='uniq_imap_msg_per_uidvalidity',
            ),
        ]

    def __str__(self):
        return f"{self.subject[:50]} from {self.from_address}"


class MailUndoLog(models.Model):
    """MAIL-162: durable record of a reversible bulk operation for bounded undo.

    Records enough prior state to reverse a move/flag/trash bulk op within a
    bounded window (default 60s, configurable via ``MAIL_UNDO_WINDOW_SECONDS``).
    Permanent delete is NOT undoable and never records an entry. Each entry
    carries per-message prior state so a mixed-state bulk flag can be reversed
    exactly.
    """

    OP_MOVE = 'move'
    OP_FLAG = 'flag'
    OP_TRASH = 'trash'
    OP_CHOICES = [
        (OP_MOVE, 'Move'),
        (OP_FLAG, 'Flag'),
        (OP_TRASH, 'Trash'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    account = models.ForeignKey(MailAccount, on_delete=models.CASCADE, related_name='undo_logs')
    # Federated auth: the mail app authenticates via Shizuha ID (FederatedUser),
    # not the local Django User table, so we store the integer user_id directly.
    user_id = models.BigIntegerField(db_index=True)
    op_type = models.CharField(max_length=10, choices=OP_CHOICES)
    message_ids = models.JSONField(default=list)
    # Per-message prior state keyed by message id string:
    #   {message_id: {'folder_id': <uuid str or None>, 'is_flagged': bool, 'is_unread': bool}}
    prior_state = models.JSONField(default=dict)
    # Target folder for move/trash (the folder the messages were moved INTO).
    target_folder_id = models.UUIDField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    undone_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'mail_undo_log'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user_id', 'expires_at']),
        ]

    def is_expired(self) -> bool:
        return timezone.now() > self.expires_at

    def is_undone(self) -> bool:
        return self.undone_at is not None

    def __str__(self):
        return f"MailUndoLog({self.op_type}, {len(self.message_ids)} msgs, undone={self.is_undone()})"


class MailAttachment(models.Model):
    """
    Attachment metadata for messages.
    Actual file content is in Stalwart (accessed via JMAP blob).
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    message = models.ForeignKey(MailMessage, on_delete=models.CASCADE, related_name='attachments')

    # JMAP identifiers
    jmap_blob_id = models.CharField(max_length=255)
    part_id = models.CharField(max_length=100, blank=True, help_text="MIME part identifier")

    # File info
    filename = models.CharField(max_length=255)
    content_type = models.CharField(max_length=100)
    size_bytes = models.IntegerField(default=0)

    # Content disposition
    is_inline = models.BooleanField(default=False)
    content_id = models.CharField(max_length=255, blank=True, help_text="Content-ID for inline images")

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'mail_attachment'
        ordering = ['filename']

    def __str__(self):
        return f"{self.filename} ({self.content_type})"


class MailDraft(models.Model):
    """
    Email drafts stored locally for auto-save functionality.
    Synced to Stalwart Drafts folder when saved.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    account = models.ForeignKey(MailAccount, on_delete=models.CASCADE, related_name='drafts')

    # Link to synced message (if saved to Stalwart)
    message = models.OneToOneField(MailMessage, null=True, blank=True, on_delete=models.SET_NULL)

    # Reply/forward context
    reply_to_message = models.ForeignKey(
        MailMessage, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='replies', help_text="Message being replied to"
    )
    is_reply = models.BooleanField(default=False)
    is_reply_all = models.BooleanField(default=False)
    is_forward = models.BooleanField(default=False)

    # Compose data
    to_addresses = models.JSONField(default=list)
    cc_addresses = models.JSONField(default=list, blank=True)
    bcc_addresses = models.JSONField(default=list, blank=True)
    subject = models.CharField(max_length=1000, blank=True)

    # Body
    body_text = models.TextField(blank=True, help_text="Plain text body")
    body_html = models.TextField(blank=True, help_text="HTML body")

    # Attachments (stored locally before send)
    attachments = models.JSONField(default=list, blank=True, help_text="List of uploaded attachment refs")
    revision = models.PositiveBigIntegerField(default=1)

    # Sync status
    is_synced = models.BooleanField(default=False, help_text="Synced to Stalwart Drafts")
    last_synced_at = models.DateTimeField(null=True, blank=True)

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'mail_draft'
        ordering = ['-updated_at']

    def __str__(self):
        return f"Draft: {self.subject[:50] or '(no subject)'}"


class MailFilter(models.Model):
    """
    Email filtering rules (synced to Stalwart Sieve scripts).
    """
    ACTION_CHOICES = [
        ('move', 'Move to Folder'),
        ('copy', 'Copy to Folder'),
        ('delete', 'Delete'),
        ('mark_read', 'Mark as Read'),
        ('mark_flagged', 'Mark as Flagged'),
        ('forward', 'Forward to Address'),
        ('label', 'Add Label'),
    ]

    MATCH_CHOICES = [
        ('all', 'Match All Conditions'),
        ('any', 'Match Any Condition'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    account = models.ForeignKey(MailAccount, on_delete=models.CASCADE, related_name='filters')

    # Rule info
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)

    # Priority/order
    priority = models.IntegerField(default=0, help_text="Lower = higher priority")

    # Conditions
    match_type = models.CharField(max_length=10, choices=MATCH_CHOICES, default='all')
    conditions = models.JSONField(default=list, help_text="List of filter conditions")

    # Actions
    actions = models.JSONField(default=list, help_text="List of actions to perform")

    # Options
    stop_processing = models.BooleanField(default=True, help_text="Stop processing further rules")

    # Status
    is_active = models.BooleanField(default=True)

    # Sync to Stalwart
    sieve_script = models.TextField(blank=True, help_text="Generated Sieve script")
    is_synced = models.BooleanField(default=False)

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'mail_filter'
        ordering = ['priority', 'name']

    def __str__(self):
        return f"{self.name} ({self.account.email_address})"


class MailContact(models.Model):
    """
    Contact/address book for autocomplete.
    Populated from sent/received emails.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    account = models.ForeignKey(MailAccount, on_delete=models.CASCADE, related_name='contacts')

    # Contact info
    email = models.EmailField()
    name = models.CharField(max_length=255, blank=True)

    # Usage tracking
    send_count = models.IntegerField(default=0, help_text="Times sent to this address")
    receive_count = models.IntegerField(default=0, help_text="Times received from this address")
    last_contacted_at = models.DateTimeField(null=True, blank=True)

    # Manual vs auto
    is_manual = models.BooleanField(default=False, help_text="Manually added contact")

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'mail_contact'
        unique_together = [['account', 'email']]
        ordering = ['-send_count', '-receive_count', 'name']

    def __str__(self):
        if self.name:
            return f"{self.name} <{self.email}>"
        return self.email


class OutboxMessage(models.Model):
    """Exactly-once send queue entry with idempotency (MAIL-59).

    Every send_email/send_draft call creates an OutboxMessage with a
    client_message_id. Retries with the same id return the cached result
    instead of re-sending.
    """
    STATUS_CHOICES = [
        ('queued', 'Queued'),
        ('sending', 'Sending'),
        ('sent', 'Sent'),
        ('failed', 'Failed'),
    ]
    DELIVERY_STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('delayed', 'Delayed'),
        ('delivered', 'Delivered'),
        ('failed', 'Failed'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    account = models.ForeignKey(MailAccount, on_delete=models.CASCADE, related_name='outbox')
    client_message_id = models.UUIDField(help_text="Client-generated idempotency key (unique per account)")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='queued')

    # Recipients
    to_addresses = models.JSONField(default=list)
    cc_addresses = models.JSONField(default=list, blank=True)
    bcc_addresses = models.JSONField(default=list, blank=True)

    # Content
    subject = models.CharField(max_length=1000, blank=True)
    body_text = models.TextField(blank=True)
    body_html = models.TextField(blank=True)

    # Attachments (refs, stored before send)
    attachments = models.JSONField(default=list, blank=True)

    # Optional draft to delete after send
    draft = models.ForeignKey(
        'MailDraft', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='outbox_entries',
    )

    # Result
    result = models.JSONField(null=True, blank=True, help_text="Send result on success")
    error = models.TextField(blank=True, help_text="Error message on failure")
    provider_started = models.BooleanField(
        null=True,
        default=None,
        help_text=(
            "Typed MAIL-59 boundary: false only when code proved provider I/O "
            "had not started; true once submission may be ambiguous; null for legacy/unknown."
        ),
    )
    delivery_status = models.CharField(
        max_length=10, choices=DELIVERY_STATUS_CHOICES, default='pending',
    )
    delivery_diagnostic = models.TextField(blank=True)
    delivery_updated_at = models.DateTimeField(null=True, blank=True)

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'mail_outbox'
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['account', 'client_message_id'],
                name='mail_outbox_account_cmi_unique',
            ),
        ]
        indexes = [
            models.Index(fields=['client_message_id'], name='mail_outbox_cmi_idx'),
            models.Index(fields=['account', 'status'], name='mail_outbox_account_status_idx'),
        ]

    def __str__(self):
        return f"Outbox {self.client_message_id}: {self.status} - {self.subject[:50]}"
