# MAIL-25: pg_trgm extension + subject/from_address GIN trigram indexes +
# threading lookup indexes + contact autocomplete indexes.
# All created CONCURRENTLY so they do not lock mail_message writes.
# atomic=False is required for CONCURRENTLY index creation.
from django.db import migrations


class Migration(migrations.Migration):

    atomic = False

    dependencies = [
        ('mail', '0009_mailmessage_search_vector'),
    ]

    operations = [
        # 1. pg_trgm extension (idempotent)
        migrations.RunSQL(
            sql='CREATE EXTENSION IF NOT EXISTS pg_trgm',
            reverse_sql=migrations.RunSQL.noop,
        ),

        # 2. Subject trigram GIN — accelerates icontains on subject
        migrations.RunSQL(
            sql=(
                'CREATE INDEX CONCURRENTLY IF NOT EXISTS mail_message_subject_trgm_idx '
                'ON mail_message USING GIN (subject gin_trgm_ops)'
            ),
            reverse_sql='DROP INDEX CONCURRENTLY IF EXISTS mail_message_subject_trgm_idx',
        ),

        # 3. from_address trigram GIN — accelerates icontains on from_address
        migrations.RunSQL(
            sql=(
                'CREATE INDEX CONCURRENTLY IF NOT EXISTS mail_message_from_trgm_idx '
                'ON mail_message USING GIN (from_address gin_trgm_ops)'
            ),
            reverse_sql='DROP INDEX CONCURRENTLY IF EXISTS mail_message_from_trgm_idx',
        ),

        # 4. message_id btree — 0001_initial creates an auto-named btree via db_index=True
        #    (models.py: message_id = CharField(db_index=True, max_length=500)).
        #    This safety-net index uses an explicit name; IF NOT EXISTS makes it idempotent
        #    regardless of whether the auto-named index already covers this column.
        migrations.RunSQL(
            sql=(
                'CREATE INDEX CONCURRENTLY IF NOT EXISTS mail_message_message_id_idx '
                'ON mail_message (message_id)'
            ),
            reverse_sql='DROP INDEX CONCURRENTLY IF EXISTS mail_message_message_id_idx',
        ),

        # 5. in_reply_to btree — no db_index on this field; explicit index for threading.
        migrations.RunSQL(
            sql=(
                'CREATE INDEX CONCURRENTLY IF NOT EXISTS mail_message_in_reply_to_idx '
                'ON mail_message (in_reply_to)'
            ),
            reverse_sql='DROP INDEX CONCURRENTLY IF EXISTS mail_message_in_reply_to_idx',
        ),

        # 6. references GIN — references is JSONB (models.py: references = JSONField(default=list)).
        #    Quoted because "references" is a PostgreSQL reserved word.
        #    Default GIN op class (jsonb_ops) enables @> containment queries for threading.
        migrations.RunSQL(
            sql=(
                'CREATE INDEX CONCURRENTLY IF NOT EXISTS mail_message_references_gin_idx '
                'ON mail_message USING GIN ("references")'
            ),
            reverse_sql='DROP INDEX CONCURRENTLY IF EXISTS mail_message_references_gin_idx',
        ),

        # 7. Composite threading index — (account_id, thread_id, date_received) for
        #    per-account thread message listing with recency ordering
        migrations.RunSQL(
            sql=(
                'CREATE INDEX CONCURRENTLY IF NOT EXISTS mail_message_account_thread_received_idx '
                'ON mail_message (account_id, thread_id, date_received)'
            ),
            reverse_sql='DROP INDEX CONCURRENTLY IF EXISTS mail_message_account_thread_received_idx',
        ),

        # 8. Contact email autocomplete — functional GIN on lower(email)
        migrations.RunSQL(
            sql=(
                'CREATE INDEX CONCURRENTLY IF NOT EXISTS mail_contact_email_trgm_idx '
                'ON mail_contact USING GIN (lower(email) gin_trgm_ops)'
            ),
            reverse_sql='DROP INDEX CONCURRENTLY IF EXISTS mail_contact_email_trgm_idx',
        ),

        # 9. Contact name autocomplete — functional GIN on lower(name)
        migrations.RunSQL(
            sql=(
                'CREATE INDEX CONCURRENTLY IF NOT EXISTS mail_contact_name_trgm_idx '
                'ON mail_contact USING GIN (lower(name) gin_trgm_ops)'
            ),
            reverse_sql='DROP INDEX CONCURRENTLY IF EXISTS mail_contact_name_trgm_idx',
        ),
    ]
