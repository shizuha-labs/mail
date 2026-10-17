from django.contrib import admin
from .models import (
    MailAccount, MailFolder, MailThread, MailMessage,
    MailAttachment, MailDraft, MailFilter, MailContact
)


@admin.register(MailAccount)
class MailAccountAdmin(admin.ModelAdmin):
    list_display = [
        'email_address', 'display_name', 'user_id', 'is_active',
        'is_default', 'connection_status', 'incoming_server_type', 'created_at'
    ]
    list_filter = ['is_active', 'is_default', 'connection_status', 'incoming_server_type']
    search_fields = ['email_address', 'display_name', 'from_name']
    readonly_fields = ['connection_status', 'connection_error', 'last_connection_check', 'created_at', 'updated_at']
    fieldsets = (
        (None, {
            'fields': ('user_id', 'email_address', 'display_name', 'from_name', 'is_active', 'is_default')
        }),
        ('Incoming Server', {
            'fields': ('incoming_server_type', 'incoming_host', 'incoming_port', 'incoming_security', 'incoming_username')
        }),
        ('Outgoing Server', {
            'fields': ('outgoing_host', 'outgoing_port', 'outgoing_security', 'outgoing_username', 'outgoing_requires_auth')
        }),
        ('Connection Status', {
            'fields': ('connection_status', 'connection_error', 'last_connection_check'),
            'classes': ('collapse',)
        }),
        ('Settings', {
            'fields': ('signature', 'settings'),
            'classes': ('collapse',)
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at', 'last_sync_at'),
            'classes': ('collapse',)
        }),
    )


@admin.register(MailFolder)
class MailFolderAdmin(admin.ModelAdmin):
    list_display = ['name', 'account', 'role', 'total_messages', 'unread_messages']
    list_filter = ['role']
    search_fields = ['name', 'account__email_address']


@admin.register(MailThread)
class MailThreadAdmin(admin.ModelAdmin):
    list_display = ['subject', 'account', 'message_count', 'unread_count', 'last_message_at']
    search_fields = ['subject', 'account__email_address']


@admin.register(MailMessage)
class MailMessageAdmin(admin.ModelAdmin):
    list_display = ['subject', 'from_address', 'folder', 'is_unread', 'date_received']
    list_filter = ['is_unread', 'is_flagged', 'has_attachments', 'folder__role']
    search_fields = ['subject', 'from_address', 'preview']
    date_hierarchy = 'date_received'


@admin.register(MailAttachment)
class MailAttachmentAdmin(admin.ModelAdmin):
    list_display = ['filename', 'content_type', 'size_bytes', 'message']
    search_fields = ['filename', 'message__subject']


@admin.register(MailDraft)
class MailDraftAdmin(admin.ModelAdmin):
    list_display = ['subject', 'account', 'is_synced', 'updated_at']
    list_filter = ['is_synced', 'is_reply', 'is_forward']
    search_fields = ['subject', 'account__email_address']


@admin.register(MailFilter)
class MailFilterAdmin(admin.ModelAdmin):
    list_display = ['name', 'account', 'priority', 'is_active', 'is_synced']
    list_filter = ['is_active', 'is_synced', 'match_type']
    search_fields = ['name', 'account__email_address']


@admin.register(MailContact)
class MailContactAdmin(admin.ModelAdmin):
    list_display = ['email', 'name', 'account', 'send_count', 'receive_count', 'is_manual']
    list_filter = ['is_manual']
    search_fields = ['email', 'name', 'account__email_address']
