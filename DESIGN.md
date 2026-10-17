# DESIGN.md - Shizuha Mail

## Overview

Shizuha Mail is a self-hosted email service that integrates with the Shizuha platform. It uses Stalwart Mail Server for the mail backend (SMTP/IMAP/JMAP) and provides a modern React-based webmail interface.

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                       Nginx (port 80/443)                       │
├─────────────────────────────────────────────────────────────────┤
│  /mail/          → React Frontend (5177)                        │
│  /mail/api/      → Django Service (8014)                        │
│  /mail/api/auth/ → shizuha-id (8001) - federated auth           │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│                 shizuha-mail (Django 4.2 + DRF)                 │
│  • Manages accounts, folders, message metadata                  │
│  • JMAP client → Stalwart for actual mail operations            │
│  • Celery tasks for background sync                             │
│  • FederatedUser auth (JWT from shizuha-id)                     │
└─────────────────────────────────────────────────────────────────┘
                              ↓ JMAP API
┌─────────────────────────────────────────────────────────────────┐
│                Stalwart Mail Server (Rust)                      │
│  • SMTP: 25, 465, 587 (inbound/outbound)                        │
│  • IMAPS: 993 (traditional clients: Thunderbird, Outlook)       │
│  • JMAP: 8080 (internal - modern API)                           │
│  • ManageSieve: 4190 (mail filters)                             │
│  • Built-in SPF, DKIM, DMARC, spam filtering                    │
│  • RocksDB storage (efficient, embedded)                        │
└─────────────────────────────────────────────────────────────────┘
```

## Data Models

### MailAccount
Links federated users (from shizuha-id) to Stalwart accounts:
- `user_id` (from JWT), `email_address`, `stalwart_account_id`
- `storage_quota_mb`, `storage_used_mb`, `settings`, `signature`

### MailFolder
Cached mailbox info (synced from Stalwart):
- `jmap_mailbox_id`, `name`, `role` (inbox/sent/drafts/trash/custom)
- `total_messages`, `unread_messages`, `parent` (hierarchy)

### MailMessage
Message metadata cache (content stored in Stalwart):
- `jmap_email_id`, `subject`, `from_address`, `to_addresses`
- `preview`, `is_unread`, `is_flagged`, `has_attachments`
- `date_sent`, `date_received`, `folder`, `thread`, `labels`

### MailThread
Conversation grouping:
- `jmap_thread_id`, `subject`, `participants`
- `message_count`, `unread_count`, `last_message_at`

### Supporting Models
- `MailAlias` - Email aliases
- `MailLabel` - User-defined labels
- `MailAttachment` - Attachment metadata
- `MailDraft` - Draft messages
- `MailFilter` - Server-side filter rules (Sieve)

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/accounts/` | GET | List user's mail accounts |
| `/api/accounts/me/` | GET | Get primary account |
| `/api/folders/` | GET | List folders |
| `/api/folders/` | POST | Create folder |
| `/api/messages/` | GET | List messages (filterable) |
| `/api/messages/{id}/` | GET | Get message details |
| `/api/messages/{id}/body/` | GET | Get message body from Stalwart |
| `/api/messages/{id}/mark_read/` | POST | Mark as read |
| `/api/messages/{id}/mark_unread/` | POST | Mark as unread |
| `/api/messages/{id}/toggle_flag/` | POST | Toggle starred |
| `/api/messages/{id}/move/` | POST | Move to folder |
| `/api/threads/{id}/` | GET | Get thread with messages |
| `/api/send/` | POST | Send email |
| `/api/search/` | GET | Search emails |
| `/api/sync/folders/` | POST | Sync folders from Stalwart |
| `/api/provision/` | POST | Provision new mail account |

## Stalwart JMAP Client

The `StalwartClient` class provides a high-level interface:

```python
client = StalwartClient(account=account, user_auth_token=token)

# Get folders
folders = client.get_folders()

# Get messages
messages = client.get_messages(mailbox_id='inbox-id', limit=50)

# Send email
client.send_email(
    from_address={'name': 'User', 'email': 'user@shizuha.com'},
    to_addresses=[{'email': 'recipient@example.com'}],
    subject='Hello',
    text_body='Message content'
)

# Move message
client.move_message(email_id, from_mailbox_id, to_mailbox_id)
```

## Frontend Structure

```
frontend/src/
├── App.jsx               # Routes and providers
├── contexts/
│   ├── AuthContext.jsx   # JWT authentication
│   └── MailContext.jsx   # Mail state (folders, selected message)
├── components/
│   ├── Layout.jsx        # Main layout with sidebar
│   ├── FolderSidebar.jsx # Folder navigation
│   ├── MessageList.jsx   # Message list with selection
│   └── ComposeModal.jsx  # Email composer
├── pages/
│   ├── Inbox.jsx         # Inbox view
│   ├── Folder.jsx        # Generic folder view
│   ├── Thread.jsx        # Conversation view
│   ├── Search.jsx        # Search results
│   └── Settings.jsx      # Account settings
└── services/
    └── api.js            # Axios API client
```

## Environment Variables

```bash
# Django
POSTGRES_DB=shizuha_mail
POSTGRES_HOST=shizuha-db
CELERY_BROKER_URL=redis://redis:6379/6
JWT_SECRET_KEY=shared-with-shizuha-id

# Stalwart
STALWART_JMAP_URL=http://stalwart-mail:8080
STALWART_ADMIN_URL=http://stalwart-mail:8080
STALWART_ADMIN_TOKEN=admin-token
MAIL_DOMAIN=shizuha.com

# Frontend
VITE_API_BASE=/mail/api
VITE_BASE_PATH=/mail/
```

## Security

- All email traffic uses TLS
- DKIM signing for outbound mail
- SPF/DMARC validation for inbound mail
- JWT authentication (federated from shizuha-id)
- Attachment size limits (25MB)
