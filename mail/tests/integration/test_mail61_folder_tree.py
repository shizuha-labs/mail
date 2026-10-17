from mail.tests.active_identity import ActiveIdentityTestCase
import uuid
from unittest.mock import MagicMock, patch

from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from mail.models import MailAccount, MailFolder, MailMessage
from mail.services.mail_service import MailServiceError
from mail.tests.helpers import create_test_jwt


class Mail61FolderTreeTest(ActiveIdentityTestCase):
    def setUp(self):
        self.client = APIClient()
        self.user_id = 61001
        token = create_test_jwt(self.user_id, 'mail61@shizuha.com')
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')

        self.account1 = MailAccount.objects.create(
            user_id=self.user_id,
            email_address='mail61-a@example.com',
            incoming_host='imap.example.com',
            outgoing_host='smtp.example.com',
            incoming_username='mail61-a@example.com',
            is_default=True,
        )
        self.account2 = MailAccount.objects.create(
            user_id=self.user_id,
            email_address='mail61-b@example.com',
            incoming_host='imap.example.com',
            outgoing_host='smtp.example.com',
            incoming_username='mail61-b@example.com',
        )
        self.foreign_account = MailAccount.objects.create(
            user_id=99991,
            email_address='mail61-foreign@example.com',
            incoming_host='imap.example.com',
            outgoing_host='smtp.example.com',
            incoming_username='mail61-foreign@example.com',
        )

        self.inbox1 = MailFolder.objects.create(
            account=self.account1, name='Inbox', role='inbox', jmap_mailbox_id='a1-inbox')
        self.archive1 = MailFolder.objects.create(
            account=self.account1, name='Archive', role='archive', jmap_mailbox_id='a1-archive')
        self.project = MailFolder.objects.create(
            account=self.account1, name='Project', role='custom', jmap_mailbox_id='a1-project')
        self.child = MailFolder.objects.create(
            account=self.account1, name='Child', role='custom', parent=self.project, jmap_mailbox_id='a1-child')
        self.inbox2 = MailFolder.objects.create(
            account=self.account2, name='Inbox', role='inbox', jmap_mailbox_id='a2-inbox')
        self.foreign_inbox = MailFolder.objects.create(
            account=self.foreign_account, name='Inbox', role='inbox', jmap_mailbox_id='foreign-inbox')

        self.child_message = MailMessage.objects.create(
            account=self.account1,
            folder=self.child,
            subject='MAIL61 child message',
            from_address='sender@example.com',
            to_addresses=[{'email': 'mail61-a@example.com'}],
            jmap_email_id='mail61-child-msg',
            size_bytes=123,
            is_unread=True,
        )
        MailMessage.objects.create(
            account=self.account2,
            folder=self.inbox2,
            subject='MAIL61 other account message',
            from_address='sender@example.com',
            to_addresses=[{'email': 'mail61-b@example.com'}],
            jmap_email_id='mail61-other-msg',
        )

    def _cached_tree(self, account):
        service = MagicMock()
        service.sync_folders.side_effect = MailServiceError('provider unavailable')
        with patch('mail.services.mail_service.get_mail_service', return_value=service):
            return self.client.get('/api/folders/tree/', {'account_id': str(account.id)})

    def test_folder_tree_is_account_scoped_stable_and_marks_special_folders(self):
        response = self._cached_tree(self.account1)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['account_id'], str(self.account1.id))
        roots = response.data['results']
        all_ids = {node['id'] for node in roots}
        all_ids.update(child['id'] for node in roots for child in node['children'])

        self.assertIn(str(self.inbox1.id), all_ids)
        self.assertIn(str(self.project.id), all_ids)
        self.assertIn(str(self.child.id), all_ids)
        self.assertNotIn(str(self.inbox2.id), all_ids)
        self.assertNotIn(str(self.foreign_inbox.id), all_ids)

        inbox = next(node for node in roots if node['id'] == str(self.inbox1.id))
        self.assertTrue(inbox['is_special'])
        self.assertEqual(inbox['special_role'], 'inbox')
        self.assertFalse(inbox['can_rename'])
        self.assertFalse(inbox['can_delete'])

        project = next(node for node in roots if node['id'] == str(self.project.id))
        self.assertTrue(project['has_children'])
        self.assertEqual(project['children'][0]['id'], str(self.child.id))
        self.assertEqual(project['children'][0]['total_messages'], 1)
        self.assertEqual(project['children'][0]['unread_messages'], 1)
        self.assertEqual(project['children'][0]['total_size_bytes'], 123)

    def test_special_folder_rename_and_delete_are_guarded(self):
        rename = self.client.patch(
            f'/api/folders/{self.inbox1.id}/',
            {'account_id': str(self.account1.id), 'name': 'Renamed Inbox'},
            format='json',
        )
        delete = self.client.delete(f'/api/folders/{self.inbox1.id}/?account_id={self.account1.id}')

        self.assertEqual(rename.status_code, status.HTTP_409_CONFLICT)
        self.assertIn('special folder', rename.data['error'])
        self.assertEqual(delete.status_code, status.HTTP_409_CONFLICT)
        self.assertIn('special folder', delete.data['error'])
        self.inbox1.refresh_from_db()
        self.assertEqual(self.inbox1.name, 'Inbox')

    def test_custom_rename_duplicate_and_delete_edges(self):
        duplicate = self.client.patch(
            f'/api/folders/{self.project.id}/',
            {'account_id': str(self.account1.id), 'name': 'Archive'},
            format='json',
        )
        self.assertEqual(duplicate.status_code, status.HTTP_409_CONFLICT)

        renamed = self.client.patch(
            f'/api/folders/{self.project.id}/',
            {'account_id': str(self.account1.id), 'name': 'Project Renamed'},
            format='json',
        )
        self.assertEqual(renamed.status_code, status.HTTP_200_OK)
        self.project.refresh_from_db()
        self.assertEqual(self.project.name, 'Project Renamed')

        non_empty_delete = self.client.delete(f'/api/folders/{self.child.id}/?account_id={self.account1.id}')
        self.assertEqual(non_empty_delete.status_code, status.HTTP_409_CONFLICT)
        self.assertIn('contains messages', non_empty_delete.data['error'])

        empty = MailFolder.objects.create(
            account=self.account1, name='Empty', role='custom', jmap_mailbox_id=f'a1-empty-{uuid.uuid4()}')
        deleted = self.client.delete(f'/api/folders/{empty.id}/?account_id={self.account1.id}')
        self.assertEqual(deleted.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(MailFolder.objects.filter(id=empty.id).exists())

    def test_move_updates_cached_folder_counts_and_search_result_folder(self):
        message = MailMessage.objects.create(
            account=self.account1,
            folder=self.inbox1,
            subject='MAIL61 move reflected in search',
            from_address='sender@example.com',
            to_addresses=[{'email': 'mail61-a@example.com'}],
            preview='folder move verification',
            jmap_email_id='mail61-move-msg',
            size_bytes=77,
            is_unread=False,
        )
        service = MagicMock()
        service.sync_folders.return_value = [
            {'jmap_mailbox_id': 'a1-inbox', 'name': 'Inbox', 'role': 'inbox', 'total_messages': 1, 'unread_messages': 0},
            {'jmap_mailbox_id': 'a1-archive', 'name': 'Archive', 'role': 'archive', 'total_messages': 0, 'unread_messages': 0},
        ]
        service.move_messages.return_value = {'moved': ['mail61-move-msg'], 'failed': {}}

        with patch('mail.services.mail_service.get_mail_service', return_value=service):
            response = self.client.post('/api/messages/move/', {
                'account_id': str(self.account1.id),
                'message_ids': ['mail61-move-msg'],
                'folder_id': str(self.archive1.id),
                'from_folder': str(self.inbox1.id),
            }, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        message.refresh_from_db()
        self.assertEqual(message.folder_id, self.archive1.id)
        service.move_messages.assert_called_once_with(
            self.account1, ['mail61-move-msg'], 'Inbox', 'Archive')

        self.inbox1.refresh_from_db()
        self.archive1.refresh_from_db()
        self.assertEqual(self.archive1.total_messages, 1)
        self.assertEqual(self.inbox1.total_messages, 0)

        search = self.client.post('/api/messages/search/', {
            'account_id': str(self.account1.id),
            'query': 'MAIL61 move reflected',
        }, format='json')
        self.assertEqual(search.status_code, status.HTTP_200_OK)
        self.assertEqual(search.data['results'][0]['folder_name'], 'Archive')

    def test_label_create_rename_delete_are_account_scoped(self):
        self.child_message.labels = ['Alpha']
        self.child_message.save(update_fields=['labels', 'updated_at'])
        other_message = MailMessage.objects.get(jmap_email_id='mail61-other-msg')
        other_message.labels = ['Alpha']
        other_message.save(update_fields=['labels', 'updated_at'])

        created = self.client.post('/api/labels/', {
            'account_id': str(self.account1.id),
            'name': 'Follow Up',
        }, format='json')
        self.assertEqual(created.status_code, status.HTTP_201_CREATED)

        listed = self.client.get('/api/labels/', {'account_id': str(self.account1.id)})
        self.assertEqual([row['name'] for row in listed.data['results']], ['Alpha', 'Follow Up'])

        with patch.object(MailMessage, 'save', side_effect=AssertionError('label rewrite must use bulk_update')):
            renamed = self.client.patch('/api/labels/Alpha/', {
                'account_id': str(self.account1.id),
                'name': 'Beta',
            }, format='json')
        self.assertEqual(renamed.status_code, status.HTTP_200_OK)
        self.child_message.refresh_from_db()
        other_message.refresh_from_db()
        self.assertEqual(self.child_message.labels, ['Beta'])
        self.assertEqual(other_message.labels, ['Alpha'])

        with patch.object(MailMessage, 'save', side_effect=AssertionError('label rewrite must use bulk_update')):
            deleted = self.client.delete(f'/api/labels/Beta/?account_id={self.account1.id}')
        self.assertEqual(deleted.status_code, status.HTTP_204_NO_CONTENT)
        self.child_message.refresh_from_db()
        other_message.refresh_from_db()
        self.assertEqual(self.child_message.labels, [])
        self.assertEqual(other_message.labels, ['Alpha'])


class Mail143TrashSourceFolderTest(ActiveIdentityTestCase):
    """MAIL-143: trash/move resolve a role-based from_folder to the right mailbox.

    The UI sends the folder role (e.g. ``sent``) as ``from_folder`` for
    role-based folders because they have no folder UUID in the route. The
    backend used to fall back to Inbox when it couldn't resolve the selector,
    so deleting from Sent moved the wrong membership — the message stayed in
    Sent and was also added to Trash. These tests pin the resolved source ref.
    """

    def setUp(self):
        self.client = APIClient()
        self.user_id = 14301
        token = create_test_jwt(self.user_id, 'mail143@shizuha.com')
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')

        self.account = MailAccount.objects.create(
            user_id=self.user_id,
            email_address='mail143@example.com',
            incoming_host='imap.example.com',
            incoming_server_type='jmap',
            outgoing_host='smtp.example.com',
            incoming_username='mail143@example.com',
            is_default=True,
        )
        self.inbox = MailFolder.objects.create(
            account=self.account, name='Inbox', role='inbox', jmap_mailbox_id='m143-inbox')
        self.sent = MailFolder.objects.create(
            account=self.account, name='Sent', role='sent', jmap_mailbox_id='m143-sent')
        self.trash = MailFolder.objects.create(
            account=self.account, name='Trash', role='trash', jmap_mailbox_id='m143-trash')

    def _service(self, folders, moved):
        service = MagicMock()
        service.sync_folders.return_value = folders
        service.move_messages.return_value = {'moved': moved, 'failed': {}}
        return service

    def test_trash_from_sent_role_resolves_to_sent_mailbox(self):
        """Deleting from Sent must move the Sent membership, not Inbox."""
        service = self._service(
            [
                {'jmap_mailbox_id': 'm143-inbox', 'name': 'Inbox', 'role': 'inbox'},
                {'jmap_mailbox_id': 'm143-sent', 'name': 'Sent', 'role': 'sent'},
                {'jmap_mailbox_id': 'm143-trash', 'name': 'Trash', 'role': 'trash'},
            ],
            ['sent-email-1'],
        )
        with patch('mail.services.mail_service.get_mail_service', return_value=service):
            response = self.client.post('/api/messages/trash/', {
                'account_id': str(self.account.id),
                'message_ids': ['sent-email-1'],
                'from_folder': 'sent',
            }, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        service.move_messages.assert_called_once_with(
            self.account, ['sent-email-1'], 'm143-sent', 'm143-trash')

    def test_trash_from_local_folder_uuid_resolves_to_server_ref(self):
        """A local MailFolder UUID as from_folder resolves to its server ref."""
        service = self._service(
            [
                {'jmap_mailbox_id': 'm143-inbox', 'name': 'Inbox', 'role': 'inbox'},
                {'jmap_mailbox_id': 'm143-sent', 'name': 'Sent', 'role': 'sent'},
                {'jmap_mailbox_id': 'm143-trash', 'name': 'Trash', 'role': 'trash'},
            ],
            ['sent-email-1'],
        )
        with patch('mail.services.mail_service.get_mail_service', return_value=service):
            response = self.client.post('/api/messages/trash/', {
                'account_id': str(self.account.id),
                'message_ids': ['sent-email-1'],
                'from_folder': str(self.sent.id),
            }, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        service.move_messages.assert_called_once_with(
            self.account, ['sent-email-1'], 'm143-sent', 'm143-trash')

    def test_trash_without_from_folder_falls_back_to_inbox(self):
        """No from_folder still defaults to Inbox (the legacy Inbox delete path)."""
        service = self._service(
            [
                {'jmap_mailbox_id': 'm143-inbox', 'name': 'Inbox', 'role': 'inbox'},
                {'jmap_mailbox_id': 'm143-trash', 'name': 'Trash', 'role': 'trash'},
            ],
            ['inbox-email-1'],
        )
        with patch('mail.services.mail_service.get_mail_service', return_value=service):
            response = self.client.post('/api/messages/trash/', {
                'account_id': str(self.account.id),
                'message_ids': ['inbox-email-1'],
            }, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        service.move_messages.assert_called_once_with(
            self.account, ['inbox-email-1'], 'm143-inbox', 'm143-trash')

    def test_move_from_sent_role_resolves_to_sent_mailbox(self):
        """Archiving/moving from Sent must use the Sent mailbox as the source."""
        service = self._service(
            [
                {'jmap_mailbox_id': 'm143-inbox', 'name': 'Inbox', 'role': 'inbox'},
                {'jmap_mailbox_id': 'm143-sent', 'name': 'Sent', 'role': 'sent'},
                {'jmap_mailbox_id': 'm143-archive', 'name': 'Archive', 'role': 'archive'},
            ],
            ['sent-email-1'],
        )
        with patch('mail.services.mail_service.get_mail_service', return_value=service):
            response = self.client.post('/api/messages/move/', {
                'account_id': str(self.account.id),
                'message_ids': ['sent-email-1'],
                'folder_role': 'archive',
                'from_folder': 'sent',
            }, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        service.move_messages.assert_called_once_with(
            self.account, ['sent-email-1'], 'm143-sent', 'm143-archive')
