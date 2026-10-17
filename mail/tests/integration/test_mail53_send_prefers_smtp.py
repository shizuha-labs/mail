from mail.tests.active_identity import ActiveIdentityTestCase
from unittest.mock import MagicMock, patch

from django.test import TestCase
from rest_framework.test import APIClient

from mail.models import MailAccount, OutboxMessage
from mail.services.mail_service import MailService, MailServiceError
from mail.tests.helpers import create_test_jwt
from mail.views import REPLY_REFERENCE_LIMIT, _normalise_reply_message_ids


class SendUsesCanonicalTransportTests(ActiveIdentityTestCase):
    def setUp(self):
        self.user_id = 53053
        self.account = MailAccount.objects.create(
            user_id=self.user_id,
            email_address='mail53@shizuha.local',
            incoming_server_type='jmap',
            incoming_host='mail.shizuha.com',
            incoming_port=8543,
            incoming_security='ssl',
            incoming_username='mail53',
            outgoing_host='mail.shizuha.com',
            outgoing_port=587,
            outgoing_security='starttls',
            outgoing_username='mail53',
        )
        self.client = APIClient()
        token = create_test_jwt(user_id=self.user_id, email=self.account.email_address)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')

    def test_jmap_incoming_with_smtp_uses_jmap_sent_agreement_path(self):
        mail_service = MagicMock()
        mail_service.send_email.return_value = {'status': 'sent', 'message_id': 'smtp-msg-1'}
        mail_service.send_email_jmap.return_value = {'status': 'sent', 'email_id': 'jmap-msg-1'}

        with patch('mail.services.mail_service.get_mail_service', return_value=mail_service):
            response = self.client.post('/api/send/', {
                'account_id': str(self.account.id),
                'to_addresses': ['mail53@shizuha.local'],
                'subject': 'MAIL-53 send should use JMAP',
                'body_text': 'jmap accepted body',
            }, format='json')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['status'], 'sent')
        self.assertEqual(response.data['email_id'], 'jmap-msg-1')
        mail_service.send_email_jmap.assert_called_once()
        mail_service.send_email.assert_not_called()

    def test_jmap_send_failure_returns_specific_actionable_consistent_state_error(self):
        mail_service = MagicMock()
        mail_service.send_email_jmap.side_effect = MailServiceError(
            'Send failed (message left in Drafts, not sent): EmailSubmission/set identityId missing'
        )

        with patch('mail.services.mail_service.get_mail_service', return_value=mail_service):
            response = self.client.post('/api/send/', {
                'account_id': str(self.account.id),
                'to_addresses': ['mail53@shizuha.local'],
                'subject': 'MAIL-53 send failure should be actionable',
                'body_text': 'jmap failure body',
            }, format='json')

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.data['status'], 'error')
        self.assertIn('Could not submit message via JMAP', response.data['error'])
        self.assertIn('Sent folder state', response.data['error'])
        self.assertIn('left in Drafts', response.data['error'])
        mail_service.send_email_jmap.assert_called_once()
        mail_service.send_email.assert_not_called()

    def test_send_without_recipient_or_reply_source_is_rejected_before_provider(self):
        mail_service = MagicMock()

        with patch('mail.services.mail_service.get_mail_service', return_value=mail_service):
            response = self.client.post('/api/send/', {
                'account_id': str(self.account.id),
                'subject': 'No destination',
                'body_text': 'must not send',
            }, format='json')

        self.assertEqual(response.status_code, 400)
        self.assertIn('to_addresses', response.json())
        self.assertEqual(OutboxMessage.objects.count(), 0)
        mail_service.get_message.assert_not_called()
        mail_service.send_email_jmap.assert_not_called()
        mail_service.send_email.assert_not_called()

    def test_compact_provider_reply_id_is_resolved_and_threaded_server_side(self):
        mail_service = MagicMock()
        mail_service.get_message.return_value = {
            'id': 'eaaaaac',
            'account_id': str(self.account.id),
            'message_id': '<parent@shizuha.local>',
            'in_reply_to': '<root@shizuha.local>',
            'references': [
                '<ancestor@shizuha.local>',
                '<root@shizuha.local>',
                '<ancestor@shizuha.local>',
            ],
            'reply_to': 'reply@shizuha.local',
            'from_address': 'sender@shizuha.local',
        }
        mail_service.send_email_jmap.return_value = {
            'status': 'sent', 'email_id': 'jmap-reply-1',
        }

        with patch('mail.services.mail_service.get_mail_service', return_value=mail_service):
            response = self.client.post('/api/send/', {
                'account_id': str(self.account.id),
                'subject': 'Re: provider id',
                'body_text': 'reply body',
                'reply_to_message_id': 'eaaaaac',
                # Caller-supplied threading is deliberately ignored.
                'in_reply_to': '<spoof@evil.test>',
                'references': ['<spoof@evil.test>'],
            }, format='json')

        self.assertEqual(response.status_code, 200)
        mail_service.get_message.assert_called_once_with(self.account, 'eaaaaac')
        mail_service.send_email_jmap.assert_called_once_with(
            account=self.account,
            to_addresses=['reply@shizuha.local'],
            subject='Re: provider id',
            body_text='reply body',
            body_html='',
            cc_addresses=[],
            bcc_addresses=[],
            in_reply_to='<parent@shizuha.local>',
            references=[
                '<ancestor@shizuha.local>',
                '<root@shizuha.local>',
                '<parent@shizuha.local>',
            ],
        )

    def test_unknown_reply_id_is_non_enumerating_and_skips_provider_send(self):
        mail_service = MagicMock()
        mail_service.get_message.return_value = None

        with patch('mail.services.mail_service.get_mail_service', return_value=mail_service):
            response = self.client.post('/api/send/', {
                'account_id': str(self.account.id),
                'to_addresses': ['reply@shizuha.local'],
                'subject': 'Re: missing',
                'body_text': 'reply body',
                'reply_to_message_id': 'foreign-or-missing-id',
            }, format='json')

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()['code'], 'reply_message_not_found')
        mail_service.get_message.assert_called_once_with(
            self.account, 'foreign-or-missing-id',
        )
        mail_service.send_email_jmap.assert_not_called()
        mail_service.send_email.assert_not_called()

    def test_invalid_derived_reply_recipient_is_rejected_before_outbox_or_send(self):
        mail_service = MagicMock()
        mail_service.get_message.return_value = {
            'id': 'eaaaaac',
            'message_id': '<parent@shizuha.local>',
            'from_address': 'not-an-email-address',
        }

        with patch('mail.services.mail_service.get_mail_service', return_value=mail_service):
            response = self.client.post('/api/send/', {
                'account_id': str(self.account.id),
                'subject': 'Re: invalid source recipient',
                'body_text': 'must not send',
                'reply_to_message_id': 'eaaaaac',
            }, format='json')

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['code'], 'reply_recipient_invalid')
        self.assertEqual(OutboxMessage.objects.count(), 0)
        mail_service.get_message.assert_called_once_with(self.account, 'eaaaaac')
        mail_service.send_email_jmap.assert_not_called()
        mail_service.send_email.assert_not_called()

    def test_cross_account_reply_id_uses_same_non_enumerating_not_found_shape(self):
        foreign = MailAccount.objects.create(
            user_id=99999,
            email_address='foreign@shizuha.local',
            incoming_server_type='jmap',
            incoming_host='mail.shizuha.com',
            incoming_port=8543,
            incoming_security='ssl',
            incoming_username='foreign',
        )
        mail_service = MagicMock()

        def account_scoped_get_message(account, message_id):
            if account == foreign and message_id == 'foreign-message-id':
                return {'id': message_id, 'message_id': '<foreign@shizuha.local>'}
            return None

        mail_service.get_message.side_effect = account_scoped_get_message
        with patch('mail.services.mail_service.get_mail_service', return_value=mail_service):
            response = self.client.post('/api/send/', {
                'account_id': str(self.account.id),
                'to_addresses': ['reply@shizuha.local'],
                'subject': 'Re: foreign',
                'body_text': 'reply body',
                'reply_to_message_id': 'foreign-message-id',
            }, format='json')

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()['code'], 'reply_message_not_found')
        mail_service.get_message.assert_called_once_with(
            self.account, 'foreign-message-id',
        )
        mail_service.send_email_jmap.assert_not_called()

    def test_imap_reply_requires_and_scopes_resolution_to_source_folder(self):
        self.account.incoming_server_type = 'imap'
        self.account.save(update_fields=['incoming_server_type'])
        mail_service = MagicMock()
        mail_service.get_message.return_value = {
            'id': '42',
            'message_id': '<imap-parent@shizuha.local>',
            'from_address': 'sender@shizuha.local',
        }
        mail_service.send_email.return_value = {
            'status': 'sent', 'message_id': '<reply@shizuha.local>',
        }

        with patch('mail.services.mail_service.get_mail_service', return_value=mail_service):
            response = self.client.post('/api/send/', {
                'account_id': str(self.account.id),
                'subject': 'Re: IMAP UID',
                'body_text': 'reply body',
                'reply_to_message_id': '42',
                'reply_to_folder_id': 'INBOX',
            }, format='json')

        self.assertEqual(response.status_code, 200)
        mail_service.get_message.assert_called_once_with(self.account, '42', 'INBOX')
        mail_service.send_email.assert_called_once()
        self.assertEqual(
            mail_service.send_email.call_args.kwargs['in_reply_to'],
            '<imap-parent@shizuha.local>',
        )

    def test_imap_reply_without_source_folder_is_non_enumerating_and_skips_provider(self):
        self.account.incoming_server_type = 'imap'
        self.account.save(update_fields=['incoming_server_type'])
        mail_service = MagicMock()

        with patch('mail.services.mail_service.get_mail_service', return_value=mail_service):
            response = self.client.post('/api/send/', {
                'account_id': str(self.account.id),
                'subject': 'Re: unscoped IMAP UID',
                'body_text': 'must not send',
                'reply_to_message_id': '42',
            }, format='json')

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()['code'], 'reply_message_not_found')
        self.assertEqual(OutboxMessage.objects.count(), 0)
        mail_service.get_message.assert_not_called()
        mail_service.send_email.assert_not_called()
        mail_service.send_email_jmap.assert_not_called()

    def test_jmap_email_set_receives_derived_thread_shapes(self):
        """The concrete JMAP boundary emits String[] threading properties."""
        service = object.__new__(MailService)
        service._credential_service = MagicMock()
        service._credential_service.decrypt_password.return_value = ''

        session = MagicMock(status_code=200)
        session.json.return_value = {
            'primaryAccounts': {
                'urn:ietf:params:jmap:mail': 'mail-account',
                'urn:ietf:params:jmap:submission': 'submit-account',
            },
        }
        mailboxes = MagicMock(status_code=200)
        mailboxes.json.return_value = {
            'methodResponses': [['Mailbox/get', {'list': [
                {'id': 'drafts', 'role': 'drafts'},
                {'id': 'sent', 'role': 'sent'},
            ]}, 'mailboxes']],
        }
        identities = MagicMock(status_code=200)
        identities.json.return_value = {
            'methodResponses': [['Identity/get', {'list': [
                {'id': 'identity-1', 'email': self.account.email_address},
            ]}, 'identities']],
        }
        submission = MagicMock(status_code=200)
        submission.json.return_value = {'methodResponses': [
            ['Email/set', {'created': {'draft': {'id': 'reply-email'}}}, 'email'],
            ['EmailSubmission/set', {'created': {'submission': {'id': 'sub-1'}}}, 'submit'],
        ]}
        client = MagicMock()
        client.__enter__.return_value = client
        client.__exit__.return_value = False
        client.get.return_value = session
        client.post.side_effect = [mailboxes, identities, submission]
        service._jmap_client = MagicMock(return_value=client)
        service._jmap_auth = MagicMock(return_value=('user', 'password'))

        result = service.send_email_jmap(
            account=self.account,
            to_addresses=['reply@shizuha.local'],
            subject='Re: provider id',
            body_text='reply body',
            in_reply_to='<parent@shizuha.local>',
            references=['<root@shizuha.local>', '<parent@shizuha.local>'],
        )

        self.assertEqual(result['email_id'], 'reply-email')
        sent_payload = client.post.call_args_list[-1].kwargs['json']
        email_create = sent_payload['methodCalls'][0][1]['create']['draft']
        self.assertEqual(email_create['inReplyTo'], ['<parent@shizuha.local>'])
        self.assertEqual(
            email_create['references'],
            ['<root@shizuha.local>', '<parent@shizuha.local>'],
        )

    def test_reply_reference_chain_is_bounded_and_keeps_parent(self):
        source_references = [f'<ref-{index}@shizuha.local>' for index in range(75)]

        parent, references = _normalise_reply_message_ids({
            'message_id': '<parent@shizuha.local>',
            'references': source_references,
        })

        self.assertEqual(parent, '<parent@shizuha.local>')
        self.assertEqual(len(references), REPLY_REFERENCE_LIMIT)
        self.assertEqual(references[-1], '<parent@shizuha.local>')
        self.assertNotIn('<ref-0@shizuha.local>', references)
