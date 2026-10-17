# MAIL-33 (MAIL-1 Slice 4): GIN trigram indexes on UPPER(subject) / UPPER(from_address)
# that ACTUALLY back Django's `__icontains`.
#
# Verify-current-state finding: Django's `__icontains` on Postgres generates
# `UPPER(col::text) LIKE UPPER(pattern)` (the planner shows `upper((subject)::text) ~~
# '%X%'`). MAIL-25's indexes (0010_trgm_threading_indexes) are on the RAW columns
# (`subject gin_trgm_ops`), which match a raw `col ILIKE pattern` (`~~*`) — NOT the
# `upper(col::text) ~~ pattern` shape Django emits. So the explicit
# from_address/to_address `icontains` filters (mail/views.py) still seq-scan despite
# MAIL-25. (The main message search uses FTS/`search_vector`, so it is unaffected.)
#
# Fix: expression GIN indexes on `UPPER(col::text) gin_trgm_ops`, matching Django's
# query shape exactly so the planner uses them. The raw MAIL-25 indexes are now
# redundant for the icontains path — see the PR for the follow-up to drop them once
# no raw-ILIKE / trigram_similar usage is confirmed.
#
# CONCURRENTLY + atomic=False (no write lock on mail_message); idempotent.
from django.db import migrations


class Migration(migrations.Migration):

    atomic = False

    dependencies = [
        ('mail', '0011_mail28_auth_type_needs_reauth'),
    ]

    operations = [
        migrations.RunSQL(
            sql=(
                'CREATE INDEX CONCURRENTLY IF NOT EXISTS mail_message_subject_upper_trgm_idx '
                'ON mail_message USING GIN (UPPER(subject::text) gin_trgm_ops)'
            ),
            reverse_sql='DROP INDEX CONCURRENTLY IF EXISTS mail_message_subject_upper_trgm_idx',
        ),
        migrations.RunSQL(
            sql=(
                'CREATE INDEX CONCURRENTLY IF NOT EXISTS mail_message_from_upper_trgm_idx '
                'ON mail_message USING GIN (UPPER(from_address::text) gin_trgm_ops)'
            ),
            reverse_sql='DROP INDEX CONCURRENTLY IF EXISTS mail_message_from_upper_trgm_idx',
        ),
    ]
