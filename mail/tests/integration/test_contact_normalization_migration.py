"""Fresh-install contact normalization must preserve the unique constraint."""
import importlib
from django.apps import apps
from django.test import TestCase
from django.utils import timezone
from mail.models import MailAccount, MailContact

class ContactNormalizationMigrationTests(TestCase):
    def test_case_variants_merge_before_rename_and_are_idempotent(self):
        account = MailAccount.objects.create(user_id=713, email_address="owner@example.org")
        other = MailAccount.objects.create(user_id=714, email_address="other@example.org")
        recent = timezone.now()
        first = MailContact.objects.create(account=account, email="Friend@Example.org", send_count=3, receive_count=1)
        MailContact.objects.create(account=account, email="friend@example.org", name="Friend", send_count=1, receive_count=4, last_contacted_at=recent)
        MailContact.objects.create(account=other, email="Friend@Example.org", send_count=9)
        migrate = importlib.import_module("mail.migrations.0022_mail163_contact_dedup")._normalize_and_merge
        migrate(apps, None)
        migrate(apps, None)
        row = MailContact.objects.get(account=account)
        self.assertEqual(row.pk, first.pk)
        self.assertEqual(row.email, "friend@example.org")
        self.assertEqual((row.send_count, row.receive_count), (4, 5))
        self.assertEqual(row.name, "Friend")
        self.assertEqual(row.last_contacted_at, recent)
        self.assertEqual(MailContact.objects.get(account=other).send_count, 9)
