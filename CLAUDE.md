# CLAUDE.md - Shizuha Mail

## Overview

**Shizuha Mail** is the email client service powered by Stalwart Mail Server (JMAP/IMAP/SMTP).

| Setting | Value |
|---------|-------|
| Backend Port | 8014 |
| Frontend Port | 5177 |
| Database | shizuha_mail |
| Redis DB | 4 |
| URL Path | `/mail/` |

## Access

| URL | Description |
|-----|-------------|
| `/mail/` | Email client UI |
| `/mail/api/` | REST API |
| `/mail/admin/` | Django Admin |

## API Endpoints

### Mailboxes
```
GET    /mail/api/mailboxes/                # List mailboxes (Inbox, Sent, etc.)
GET    /mail/api/mailboxes/{id}/messages/  # Messages in mailbox
```

### Messages
```
GET    /mail/api/messages/{id}/            # Message details
POST   /mail/api/messages/send/            # Send email
PATCH  /mail/api/messages/{id}/            # Mark read/unread, move
DELETE /mail/api/messages/{id}/            # Delete message
```

### Contacts
```
GET/POST   /mail/api/contacts/             # Contact list
GET/PATCH  /mail/api/contacts/{id}/        # Contact details
```

### Search
```
GET /mail/api/search/?q=<query>            # Search emails
```

## Architecture

```
┌─────────────────┐     ┌─────────────────┐
│  shizuha-mail   │────►│    Stalwart     │
│  (Django API)   │JMAP │  (Mail Server)  │
└─────────────────┘     └─────────────────┘
                              │
                        ┌─────┴─────┐
                        │ IMAP/SMTP │
                        └───────────┘
```

## Stalwart Ports

| Port | Protocol | Purpose |
|------|----------|---------|
| 25 | SMTP | Incoming mail |
| 465 | SMTPS | Secure SMTP |
| 587 | Submission | Email sending |
| 993 | IMAPS | Email clients |
| 8080 | JMAP | API access |

## Key Models

- **MailAccount** - User's email account link
- **Contact** - Address book
- **Signature** - Email signatures
- **Filter** - Mail rules

## Commands

All development commands run from the `compose/` directory using the master docker-compose.yaml:

```bash
# Start service with frontend
cd compose && docker compose up -d shizuha-mail shizuha-mail-frontend

# Run migrations
cd compose && docker compose exec shizuha-mail python manage.py migrate

# Run tests
cd compose && docker compose exec shizuha-mail python manage.py test

# View logs
cd compose && docker compose logs -f shizuha-mail

```

## Project Structure

```
shizuha-mail/
├── mailmanager/          # Django project settings
├── mail/                 # Main app
│   ├── models.py         # MailAccount, Contact, Signature
│   ├── views.py          # DRF ViewSets
│   └── jmap_client.py    # Stalwart JMAP integration
└── frontend/             # React email client UI
```

Also reference [../CLAUDE.md](../CLAUDE.md) for platform-wide rules.
