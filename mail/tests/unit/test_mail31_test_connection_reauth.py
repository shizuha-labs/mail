"""MAIL-31: the existing-account "Test Connection" action must pass auth_type so an
OAuth account whose token expired returns needs_reauth (not auth_failed) on a manual
test, matching the sync-task path (MAIL-28). Regression: password accounts unchanged.
"""
from unittest import mock

from django.test import TestCase
from rest_framework.test import APIRequestFactory

from mail.models import MailAccount
from mail.views import MailAccountViewSet


def _account(email, auth_type='password'):
    return MailAccount.objects.create(
        user_id=1,
        email_address=email,
        incoming_host='imap.example.com',
        outgoing_host='smtp.example.com',
        incoming_username=email,
        auth_type=auth_type,
    )


class TestConnectionReauthViewTests(TestCase):
    """The `test` action (existing-account Test Connection, views.py) must wire
    account.auth_type into classify_connection_error so OAuth expiry → needs_reauth."""

    def _run_test_action(self, auth_type, incoming_message):
        account = _account(f'{auth_type}@example.com', auth_type=auth_type)
        view = MailAccountViewSet()
        view.get_object = mock.Mock(return_value=account)
        request = APIRequestFactory().post(f'/api/accounts/{account.id}/test/')
        view.request = request
        view.kwargs = {'pk': str(account.id)}
        view.format_kwarg = None
        # incoming auth failure, outgoing fine → exercises the views.py:325 classifier branch
        with mock.patch('mail.services.mail_service.get_mail_service') as get_svc:
            get_svc.return_value.test_connection.return_value = {
                'incoming': {'success': False, 'message': incoming_message},
                'outgoing': {'success': True},
            }
            view.test(request, pk=str(account.id))
        account.refresh_from_db()
        return account.connection_status

    def test_oauth_token_expiry_returns_needs_reauth(self):
        status = self._run_test_action(
            'oauth2', '[AUTHENTICATIONFAILED] Invalid credentials (token expired)'
        )
        self.assertEqual(status, 'needs_reauth')

    def test_password_auth_failure_still_returns_auth_failed(self):
        status = self._run_test_action(
            'password', '[AUTHENTICATIONFAILED] Invalid credentials'
        )
        self.assertEqual(status, 'auth_failed')
