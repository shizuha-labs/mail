# Shizuha Mail - E2E Testing Strategy

## Overview

Testing email in a dev environment (D1) when production domain (shizuha.com) points to a different machine (M1) on the same VPN.

```
┌─────────────────────────────────────────────────────────────────┐
│                    Development Network (VPN)                     │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌─────────────┐              ┌─────────────┐                   │
│  │     M1      │              │     D1      │                   │
│  │ (Production)│              │    (Dev)    │                   │
│  │             │              │             │                   │
│  │ shizuha.com │              │ dev.local   │                   │
│  │ points here │              │ test here   │                   │
│  └─────────────┘              └─────────────┘                   │
│                                     │                            │
│                     ┌───────────────┼───────────────┐           │
│                     │               │               │           │
│                     ▼               ▼               ▼           │
│              ┌──────────┐   ┌──────────┐   ┌──────────┐        │
│              │ Stalwart │   │  Django  │   │ Mailpit  │        │
│              │   Mail   │──▶│   API    │──▶│ (SMTP    │        │
│              │  Server  │   │          │   │  Catcher)│        │
│              └──────────┘   └──────────┘   └──────────┘        │
│                                                   │              │
│                                                   ▼              │
│                                            ┌──────────┐         │
│                                            │ Web UI   │         │
│                                            │ :8025    │         │
│                                            │ View all │         │
│                                            │ captured │         │
│                                            │ emails   │         │
│                                            └──────────┘         │
└─────────────────────────────────────────────────────────────────┘
```

## Testing Layers

### Layer 1: Unit Tests (Fast, Mocked)
- Mock JMAP client responses
- Test serializers, models, business logic
- No external dependencies

### Layer 2: Integration Tests (Django + Stalwart + Mailpit)
- Real Stalwart JMAP calls
- Emails captured by Mailpit
- Verify via Mailpit API

### Layer 3: E2E Tests (Full UI with Playwright)
- Test complete user flows
- Login → Compose → Send → Verify in Mailpit
- Verify in Sent folder

---

## Setup: Mail Catcher (Mailpit)

We use **Mailpit** (modern MailHog alternative) to capture all outbound SMTP:

```yaml
# docker-compose.yml - mailpit service
mailpit:
  image: axllent/mailpit:latest
  container_name: mailpit
  ports:
    - "8025:8025"  # Web UI
    - "1025:1025"  # SMTP
  environment:
    - MP_SMTP_AUTH_ACCEPT_ANY=true
    - MP_SMTP_AUTH_ALLOW_INSECURE=true
  networks:
    - shizuha-network
```

### Mailpit Features
- **Web UI**: http://localhost:8025 - view all captured emails
- **API**: http://localhost:8025/api/v1/messages - programmatic access
- **SMTP**: Port 1025 - accepts all mail
- **Search**: Filter by recipient, subject, date
- **HTML Preview**: Render HTML emails

---

## Configuration: Development vs Production

### Environment Variable
```bash
# .env
MAIL_ENV=dev  # or 'prod'
```

### Stalwart Config Selection
```yaml
# docker-compose.yml
stalwart-mail:
  volumes:
    - ./stalwart/config.${MAIL_ENV:-dev}.toml:/opt/stalwart-mail/etc/config.toml:ro
```

### Dev Config: stalwart/config.dev.toml
```toml
# Relay all outbound to Mailpit instead of real SMTP
[queue.outbound]
next-hop = [
  { host = "mailpit", port = 1025, protocol = "smtp" }
]

# Skip authentication validation (dev only)
[auth.spf]
verify = "disable"

[auth.dkim]
verify = "disable"

[auth.dmarc]
verify = "disable"

# Use dev domain
[[directory."internal".domains]]
name = "dev.shizuha.local"
catch-all = true
```

### Prod Config: stalwart/config.prod.toml
```toml
# Real SMTP delivery
[queue.outbound]
hostname = "mail.shizuha.com"

# Full authentication
[auth.spf]
verify = "relaxed"

[auth.dkim]
verify = "relaxed"

[auth.dmarc]
verify = "relaxed"
```

---

## Test Account Setup

### Development Test Accounts
```bash
# Create test accounts in Stalwart
docker compose exec stalwart-mail \
  stalwart-cli account create test@dev.shizuha.local --password testpass123

docker compose exec stalwart-mail \
  stalwart-cli account create sender@dev.shizuha.local --password testpass123

docker compose exec stalwart-mail \
  stalwart-cli account create recipient@dev.shizuha.local --password testpass123
```

### Test User JWT
```python
# Generate test JWT for FederatedUser
from mail.authentication import create_test_jwt

token = create_test_jwt(
    user_id="test-user-123",
    email="test@dev.shizuha.local",
    username="testuser"
)
```

---

## Running Tests

### 1. Unit Tests (No Docker required for external services)
```bash
docker compose exec shizuha-mail python manage.py test mail.tests.unit
```

### 2. Integration Tests (Requires Mailpit)
```bash
# Start dependencies
docker compose up -d mailpit stalwart-mail shizuha-mail

# Run integration tests
docker compose exec shizuha-mail python manage.py test mail.tests.integration
```

### 3. E2E Tests (Full stack)
```bash
# Start all services
docker compose up -d

# Run Playwright tests
docker compose -f docker-compose.yml -f docker-compose.test.yml \
  run --rm mail-tests
```

### 4. Manual Testing via Mailpit UI
```bash
# Open Mailpit web interface
open http://localhost:8025

# Send test email via Django shell
docker compose exec shizuha-mail python manage.py shell
>>> from mail.services.stalwart_client import StalwartClient
>>> client = StalwartClient.for_test_user()
>>> client.send_email(
...     to_addresses=[{'email': 'anyone@example.com'}],
...     subject='Test Email',
...     text_body='Hello from dev!'
... )

# Check Mailpit UI - email should appear
```

---

## Test Scenarios

### Email Sending Tests
| Test | Description | Verification |
|------|-------------|--------------|
| `test_send_simple_email` | Send plain text email | Mailpit API shows message |
| `test_send_html_email` | Send HTML email | Mailpit shows HTML rendered |
| `test_send_with_attachment` | Send with PDF attachment | Mailpit shows attachment |
| `test_send_to_multiple` | Send to multiple recipients | Mailpit shows all recipients |
| `test_send_cc_bcc` | Send with CC and BCC | Verify BCC not in headers |
| `test_reply_threading` | Reply preserves thread | In-Reply-To header set |
| `test_forward_email` | Forward includes original | Body contains forwarded content |

### Email Receiving Tests (via Stalwart JMAP)
| Test | Description | Verification |
|------|-------------|--------------|
| `test_inbox_fetch` | List inbox messages | Messages returned from JMAP |
| `test_message_body` | Fetch message body | HTML/text body returned |
| `test_mark_read` | Mark message as read | `\Seen` flag set |
| `test_move_to_folder` | Move to trash | Message in trash folder |
| `test_delete_message` | Permanently delete | Message removed |
| `test_search` | Search by subject | Matching messages returned |

### E2E User Flow Tests
| Test | Description | Steps |
|------|-------------|-------|
| `test_compose_send_flow` | Complete send flow | Login → Compose → Fill → Send → Check Sent |
| `test_read_reply_flow` | Read and reply | Open message → Reply → Send → Check thread |
| `test_folder_navigation` | Navigate folders | Click Sent → Click Drafts → Click Inbox |
| `test_search_flow` | Search and open | Search term → Click result → View message |
| `test_delete_flow` | Delete message | Select → Delete → Verify in Trash |

---

## Mailpit API Reference

### List All Messages
```bash
curl http://localhost:8025/api/v1/messages
```

### Get Message by ID
```bash
curl http://localhost:8025/api/v1/message/{id}
```

### Search Messages
```bash
curl "http://localhost:8025/api/v1/search?query=subject:Test"
```

### Delete All Messages (Clean Slate)
```bash
curl -X DELETE http://localhost:8025/api/v1/messages
```

### Python Helper
```python
# mail/tests/helpers.py
import requests

class MailpitClient:
    def __init__(self, base_url='http://mailpit:8025'):
        self.base_url = base_url

    def get_messages(self):
        """Get all captured messages"""
        r = requests.get(f'{self.base_url}/api/v1/messages')
        return r.json()['messages']

    def get_message(self, id):
        """Get single message with body"""
        r = requests.get(f'{self.base_url}/api/v1/message/{id}')
        return r.json()

    def search(self, query):
        """Search messages"""
        r = requests.get(f'{self.base_url}/api/v1/search', params={'query': query})
        return r.json()['messages']

    def clear(self):
        """Delete all messages"""
        requests.delete(f'{self.base_url}/api/v1/messages')

    def wait_for_message(self, subject, timeout=10):
        """Wait for message with subject to appear"""
        import time
        start = time.time()
        while time.time() - start < timeout:
            messages = self.search(f'subject:{subject}')
            if messages:
                return messages[0]
            time.sleep(0.5)
        raise TimeoutError(f'Message with subject "{subject}" not found')
```

---

## CI/CD Integration

### GitHub Actions
```yaml
name: Mail Service Tests

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest

    steps:
      - uses: actions/checkout@v4

      - name: Start services
        run: |
          docker compose up -d mailpit stalwart-mail shizuha-mail shizuha-mail-frontend
          sleep 10  # Wait for services

      - name: Run unit tests
        run: |
          docker compose exec -T shizuha-mail python manage.py test mail.tests.unit

      - name: Run integration tests
        run: |
          docker compose exec -T shizuha-mail python manage.py test mail.tests.integration

      - name: Run E2E tests
        run: |
          docker compose -f docker-compose.yml -f docker-compose.test.yml \
            run --rm mail-tests

      - name: Upload test artifacts
        if: failure()
        uses: actions/upload-artifact@v3
        with:
          name: playwright-report
          path: shizuha-mail/frontend/playwright-report/
```

---

## Troubleshooting

### Emails not appearing in Mailpit
1. Check Stalwart is configured to relay to Mailpit
2. Verify Mailpit is running: `docker compose logs mailpit`
3. Check Stalwart queue: `docker compose exec stalwart-mail stalwart-cli queue list`

### JMAP connection refused
1. Ensure Stalwart is running: `docker compose ps stalwart-mail`
2. Check JMAP port: `curl http://localhost:8080/.well-known/jmap`
3. Verify Django can reach Stalwart: `docker compose exec shizuha-mail curl http://stalwart-mail:8080`

### Test user authentication fails
1. Verify test account exists in Stalwart
2. Check JWT secret matches between services
3. Test token generation: `python manage.py shell` → `from mail.authentication import ...`

### Playwright tests timeout
1. Ensure frontend is built and running
2. Check BASE_URL is accessible from test container
3. Increase timeout in playwright.config.js

---

## Directory Structure

```
shizuha-mail/
├── mail/
│   └── tests/
│       ├── __init__.py
│       ├── helpers.py          # MailpitClient, test utilities
│       ├── unit/
│       │   ├── __init__.py
│       │   ├── test_models.py
│       │   ├── test_serializers.py
│       │   └── test_jmap_client.py
│       └── integration/
│           ├── __init__.py
│           ├── test_send_email.py
│           ├── test_receive_email.py
│           └── test_folders.py
├── frontend/
│   ├── tests/
│   │   ├── e2e/
│   │   │   ├── compose.spec.js
│   │   │   ├── inbox.spec.js
│   │   │   ├── search.spec.js
│   │   │   └── settings.spec.js
│   │   └── fixtures/
│   │       └── test-user.json
│   └── playwright.config.js
└── TESTING.md                   # This file
```
