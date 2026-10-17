"""MAIL-33 (MAIL-1 Slice 4): EXPLAIN regression — the explicit subject/from_address
`icontains` filter paths must use the MAIL-25 GIN trigram indexes, not a seqscan.

Context (verify-current-state): the main message search already uses Postgres FTS
(`search_vector`, views.py) — the `icontains` OR-chain there is the non-Postgres
test/dev fallback. The remaining real-prod `icontains` paths are the explicit
`from_address`/`subject` filters. `from_address` (and `subject`) are backed by
MAIL-25's GIN trigram indexes; `to_addresses` is JSONB and already FTS-covered at
weight B (MAIL-19), so it needs no separate index. This test locks in that the GIN
indexes actually back the subject/from_address substring filters, guarding against a
future change that silently drops index usage.

Postgres-only (GIN trigram); skipped on sqlite.

Why TransactionTestCase + seeded rows + ANALYZE (review MAIL-33 P1/P2):
- On an EMPTY table the planner costs a seq scan at ~0 regardless of
  `enable_seqscan = off`, so it would emit `Seq Scan`/`Result` (no index name) and
  the assertion would fail on every clean CI run. We seed a batch of rows and run
  `ANALYZE` so the planner has real statistics and is actually steered to the GIN
  index by the GUC.
- `SET LOCAL enable_seqscan = off` is transaction-scoped. Under `TestCase` (savepoint
  rollback, shared outer transaction) the GUC would leak into later methods; using
  `TransactionTestCase` (full per-test reset, no shared transaction) avoids that
  leak entirely. `_plan_for` also restores the GUC defensively.
"""
from django.db import connection, transaction
from django.test import TransactionTestCase

from mail.models import MailAccount, MailFolder, MailMessage


class Mail33ExplainGinIndexTests(TransactionTestCase):
    def setUp(self):
        if connection.vendor != 'postgresql':
            self.skipTest('GIN trigram indexes are Postgres-only')
        self._seed()

    def _seed(self):
        """Populate the table with enough rows (and run ANALYZE) that the planner
        has non-zero statistics — otherwise an empty table makes a seq scan free
        and the GUC cannot steer the plan to the index."""
        account = MailAccount.objects.create(
            user_id=1,
            display_name='Test',
            email_address='owner@example.com',
            incoming_host='imap.example.com',
            incoming_username='owner@example.com',
            encrypted_incoming_password=b'x',
            outgoing_host='smtp.example.com',
            outgoing_username='owner@example.com',
            encrypted_outgoing_password=b'x',
        )
        folder = MailFolder.objects.create(
            account=account, jmap_mailbox_id='inbox', name='Inbox',
        )
        msgs = []
        for i in range(40):
            msgs.append(MailMessage(
                account=account,
                folder=folder,
                subject=f'Invoice {i} for project alpha' if i % 2 == 0 else f'Re: meeting notes {i}',
                from_address=f'alice{i}@example.com' if i % 2 == 0 else f'bob{i}@example.org',
                to_addresses=[],
            ))
        MailMessage.objects.bulk_create(msgs)
        with connection.cursor() as cur:
            cur.execute('ANALYZE mail_message')

    def _plan_for(self, **filter_kwargs):
        qs = MailMessage.objects.filter(**filter_kwargs)
        sql, params = qs.query.sql_with_params()
        with transaction.atomic(), connection.cursor() as cur:
            # SET LOCAL must share the EXPLAIN transaction even under autocommit.
            # Force index consideration: even with statistics, on a small table the
            # planner may still prefer a seq scan; disabling it makes any valid index
            # path win. Scoped to this cursor's transaction; restored below.
            cur.execute('SET LOCAL enable_seqscan = off')
            try:
                cur.execute('EXPLAIN ' + sql, params)
                return '\n'.join(row[0] for row in cur.fetchall())
            finally:
                cur.execute('SET LOCAL enable_seqscan = on')

    def test_subject_icontains_uses_gin_index(self):
        plan = self._plan_for(subject__icontains='invoice')
        self.assertIn('mail_message_subject_upper_trgm_idx', plan)
        self.assertNotIn('Seq Scan on mail_message', plan)

    def test_from_address_icontains_uses_gin_index(self):
        plan = self._plan_for(from_address__icontains='alice@example.com')
        self.assertIn('mail_message_from_upper_trgm_idx', plan)
        self.assertNotIn('Seq Scan on mail_message', plan)
