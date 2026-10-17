# ROADMAP.md - Shizuha Mail

## Current Status: v0.1.0 (Initial Implementation)

### Completed Features
- [x] Django backend with DRF
- [x] Stalwart Mail Server integration
- [x] JMAP client implementation
- [x] Core data models (Account, Folder, Message, Thread)
- [x] REST API endpoints
- [x] React frontend with Tailwind CSS
- [x] Authentication (FederatedUser pattern)
- [x] Basic email operations (read, send, move, delete)
- [x] Compose modal
- [x] Folder navigation
- [x] Message list and thread view
- [x] Search functionality
- [x] Settings page

### In Progress
- [ ] Full sync with Stalwart
- [ ] Attachment upload/download
- [ ] Real-time updates (WebSocket)

---

## Short-term (v0.2.0)

### High Priority
- [ ] Attachment handling (upload, download, inline images)
- [ ] Rich text composer (TipTap or similar)
- [ ] Email drafts auto-save
- [ ] Conversation threading improvements
- [ ] Bulk operations (select all, move multiple)

### Medium Priority
- [ ] Mail filters UI (create/edit Sieve rules)
- [ ] Labels/tags for messages
- [ ] Contact suggestions (autocomplete)
- [ ] Keyboard shortcuts
- [ ] Email templates

---

## Medium-term (v0.3.0)

### Features
- [ ] Push notifications for new mail
- [ ] Desktop notifications (browser)
- [ ] Mobile-responsive design improvements
- [ ] Dark mode
- [ ] Custom folder colors/icons
- [ ] Vacation auto-responder
- [ ] Forwarding rules
- [ ] Blocked senders list

### Integrations
- [ ] Save email to Shizuha Notes
- [ ] Create task from email (Shizuha Pulse)
- [ ] Contact sync with Shizuha Admin
- [ ] Calendar invites (Shizuha Calendar, future)

---

## Long-term (v1.0.0)

### Advanced Features
- [ ] AI-powered features
  - [ ] Smart compose suggestions
  - [ ] Email categorization
  - [ ] Priority inbox
  - [ ] Auto-reply suggestions
- [ ] End-to-end encryption (PGP)
- [ ] S/MIME support
- [ ] Calendar integration (ICS)
- [ ] Contact management (CardDAV)

### Enterprise Features
- [ ] Multiple accounts
- [ ] Shared mailboxes
- [ ] Email delegation
- [ ] Compliance/archiving
- [ ] Audit logs
- [ ] Admin dashboard for mail server

---

## Technical Debt

### Performance
- [ ] Message list virtualization
- [ ] Lazy loading for attachments
- [ ] Caching improvements
- [ ] Background sync optimization

### Testing
- [ ] Unit tests for JMAP client
- [ ] API integration tests
- [ ] E2E tests with Playwright
- [ ] Load testing

### Documentation
- [ ] API documentation (OpenAPI)
- [ ] Deployment guide
- [ ] DNS setup guide
- [ ] Troubleshooting guide

---

## Recently Completed

### v0.1.0 (January 2026)
- Initial implementation
- Stalwart Mail Server integration
- Core email functionality
- React frontend
