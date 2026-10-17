from mail.tests.active_identity import ActiveIdentityTestCase
"""MAIL-162: bulk message operations — bounded undo for move/flag/trash.

Covers the undo contract:
- reversible ops (move/flag/trash) record an undo log and return undo_id;
- undo restores prior per-message state within the window;
- expired / already-applied undo entries are rejected;
- permanent delete never records an undo log (non-destructive default).
"""
import uuid
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from mail.models import MailAccount, MailFolder, MailMessage, MailUndoLog
from mail.tests.helpers import create_test_jwt


class Mail162UndoTest(ActiveIdentityTestCase):
    def setUp(self):
        self.client = APIClient()
        self.user_id = 16201
        token = create_test_jwt(self.user_id, 'mail162@shizuha.com')
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')

        self.account = MailAccount.objects.create(
            user_id=self.user_id,
            email_address='mail162@example.com',
            incoming_host='imap.example.com',
            outgoing_host='smtp.example.com',
            incoming_username='mail162@example.com',
            is_default=True,
        )
        self.inbox = MailFolder.objects.create(
            account=self.account, name='Inbox', role='inbox', jmap_mailbox_id='m162-inbox')
        self.archive = MailFolder.objects.create(
            account=self.account, name='Archive', role='archive', jmap_mailbox_id='m162-archive')
        self.trash = MailFolder.objects.create(
            account=self.account, name='Trash', role='trash', jmap_mailbox_id='m162-trash')

        self.msg1 = MailMessage.objects.create(
            account=self.account, folder=self.inbox,
            subject='MAIL162 one', from_address='a@example.com',
            jmap_email_id='m162-msg-1', is_flagged=False, is_unread=True,
        )
        self.msg2 = MailMessage.objects.create(
            account=self.account, folder=self.inbox,
            subject='MAIL162 two', from_address='b@example.com',
            jmap_email_id='m162-msg-2', is_flagged=False, is_unread=True,
        )

    def _service(self, moved=None):
        service = MagicMock()
        service.sync_folders.return_value = [
            {'jmap_mailbox_id': 'm162-inbox', 'name': 'Inbox', 'role': 'inbox'},
            {'jmap_mailbox_id': 'm162-archive', 'name': 'Archive', 'role': 'archive'},
            {'jmap_mailbox_id': 'm162-trash', 'name': 'Trash', 'role': 'trash'},
        ]
        service.move_messages.return_value = {'moved': moved or [], 'failed': {}}
        service.set_message_flags.return_value = {'updated': []}
        return service

    def test_flag_records_undo_log_and_returns_undo_id(self):
        service = self._service()
        with patch('mail.services.mail_service.get_mail_service', return_value=service):
            resp = self.client.post('/api/messages/flag/', {
                'account_id': str(self.account.id),
                'message_ids': [str(self.msg1.id), str(self.msg2.id)],
                'is_flagged': True,
            }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIsNotNone(resp.data.get('undo_id'))
        entry = MailUndoLog.objects.get(id=resp.data['undo_id'])
        self.assertEqual(entry.op_type, MailUndoLog.OP_FLAG)
        self.assertEqual(len(entry.message_ids), 2)
        # Prior state captured before the flag flip.
        self.assertFalse(entry.prior_state[str(self.msg1.id)]['is_flagged'])

    def test_flag_undo_restores_prior_state(self):
        service = self._service()
        with patch('mail.services.mail_service.get_mail_service', return_value=service):
            resp = self.client.post('/api/messages/flag/', {
                'account_id': str(self.account.id),
                'message_ids': [str(self.msg1.id)],
                'is_flagged': True,
            }, format='json')
            undo_id = resp.data['undo_id']
            undo_resp = self.client.post('/api/messages/undo/', {
                'undo_id': undo_id,
            }, format='json')
        self.assertEqual(undo_resp.status_code, status.HTTP_200_OK)
        self.assertEqual(undo_resp.data['status'], 'undone')
        self.assertIn(str(self.msg1.id), undo_resp.data['undone'])
        # set_message_flags was called with the prior (unflagged) state AND the
        # prior folder_id — required for IMAP flag updates (MAIL-162 P2).
        service.set_message_flags.assert_called_with(
            self.account, [str(self.msg1.id)],
            folder_id=str(self.inbox.id),
            is_unread=True, is_flagged=False,
        )
        entry = MailUndoLog.objects.get(id=undo_id)
        self.assertTrue(entry.is_undone())

    def test_flag_undo_records_prior_folder_id(self):
        # The flag undo log must record the message's folder_id so the undo
        # path can pass it to set_message_flags for IMAP accounts.
        service = self._service()
        with patch('mail.services.mail_service.get_mail_service', return_value=service):
            resp = self.client.post('/api/messages/flag/', {
                'account_id': str(self.account.id),
                'message_ids': [str(self.msg1.id)],
                'is_flagged': True,
            }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        entry = MailUndoLog.objects.get(id=resp.data['undo_id'])
        self.assertEqual(
            entry.prior_state[str(self.msg1.id)]['folder_id'],
            str(self.inbox.id),
        )

    def test_move_records_undo_log_with_prior_folder(self):
        service = self._service(moved=['m162-msg-1'])
        with patch('mail.services.mail_service.get_mail_service', return_value=service):
            resp = self.client.post('/api/messages/move/', {
                'account_id': str(self.account.id),
                'message_ids': ['m162-msg-1'],
                'folder_id': str(self.archive.id),
                'from_folder': str(self.inbox.id),
            }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIsNotNone(resp.data.get('undo_id'))
        entry = MailUndoLog.objects.get(id=resp.data['undo_id'])
        self.assertEqual(entry.op_type, MailUndoLog.OP_MOVE)
        self.assertEqual(str(entry.target_folder_id), str(self.archive.id))
        self.assertEqual(entry.prior_state['m162-msg-1']['folder_id'], str(self.inbox.id))

    def test_trash_records_undo_log(self):
        service = self._service(moved=['m162-msg-2'])
        with patch('mail.services.mail_service.get_mail_service', return_value=service):
            resp = self.client.post('/api/messages/trash/', {
                'account_id': str(self.account.id),
                'message_ids': ['m162-msg-2'],
                'from_folder': str(self.inbox.id),
            }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIsNotNone(resp.data.get('undo_id'))
        entry = MailUndoLog.objects.get(id=resp.data['undo_id'])
        self.assertEqual(entry.op_type, MailUndoLog.OP_TRASH)
        self.assertEqual(str(entry.target_folder_id), str(self.trash.id))

    def test_expired_undo_rejected(self):
        service = self._service()
        with patch('mail.services.mail_service.get_mail_service', return_value=service):
            resp = self.client.post('/api/messages/flag/', {
                'account_id': str(self.account.id),
                'message_ids': [str(self.msg1.id)],
                'is_flagged': True,
            }, format='json')
            undo_id = resp.data['undo_id']
        MailUndoLog.objects.filter(id=undo_id).update(
            expires_at=timezone.now() - timezone.timedelta(seconds=1))
        undo_resp = self.client.post('/api/messages/undo/', {'undo_id': undo_id}, format='json')
        self.assertEqual(undo_resp.status_code, status.HTTP_410_GONE)

    def test_already_undone_rejected(self):
        service = self._service()
        with patch('mail.services.mail_service.get_mail_service', return_value=service):
            resp = self.client.post('/api/messages/flag/', {
                'account_id': str(self.account.id),
                'message_ids': [str(self.msg1.id)],
                'is_flagged': True,
            }, format='json')
            undo_id = resp.data['undo_id']
            self.client.post('/api/messages/undo/', {'undo_id': undo_id}, format='json')
            second = self.client.post('/api/messages/undo/', {'undo_id': undo_id}, format='json')
        self.assertEqual(second.status_code, status.HTTP_409_CONFLICT)

    def test_foreign_user_cannot_undo(self):
        service = self._service()
        with patch('mail.services.mail_service.get_mail_service', return_value=service):
            resp = self.client.post('/api/messages/flag/', {
                'account_id': str(self.account.id),
                'message_ids': [str(self.msg1.id)],
                'is_flagged': True,
            }, format='json')
            undo_id = resp.data['undo_id']
        other = APIClient()
        other.credentials(HTTP_AUTHORIZATION=f'Bearer {create_test_jwt(99991, "other@shizuha.com")}')
        undo_resp = other.post('/api/messages/undo/', {'undo_id': undo_id}, format='json')
        self.assertEqual(undo_resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_permanent_delete_never_records_undo_log(self):
        service = self._service()
        service.delete_messages.return_value = {'deleted': ['m162-msg-1'], 'failed': {}}
        with patch('mail.services.mail_service.get_mail_service', return_value=service):
            resp = self.client.post('/api/messages/delete/', {
                'account_id': str(self.account.id),
                'message_ids': ['m162-msg-1'],
            }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIsNone(resp.data.get('undo_id'))
        self.assertEqual(MailUndoLog.objects.filter(op_type=MailUndoLog.OP_TRASH).count(), 0)
