# Generated for MAIL-17: local Postgres FTS over cached mail metadata.
from django.db import migrations
import django.contrib.postgres.search


CREATE_TRIGGER = r'''
CREATE OR REPLACE FUNCTION mail_message_search_vector_update() RETURNS trigger AS $$
BEGIN
    NEW.search_vector :=
        setweight(to_tsvector('simple', coalesce(NEW.subject, '')), 'A') ||
        setweight(to_tsvector('simple', coalesce(NEW.from_address, '') || ' ' || coalesce(NEW.from_name, '')), 'B') ||
        setweight(to_tsvector('simple', coalesce(NEW.to_addresses::text, '') || ' ' || coalesce(NEW.cc_addresses::text, '')), 'B') ||
        setweight(to_tsvector('simple', coalesce(NEW.preview, '')), 'C');
    RETURN NEW;
END
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS mail_message_search_vector_update ON mail_message;
CREATE TRIGGER mail_message_search_vector_update
BEFORE INSERT OR UPDATE OF subject, from_address, from_name, to_addresses, cc_addresses, preview
ON mail_message
FOR EACH ROW EXECUTE FUNCTION mail_message_search_vector_update();

UPDATE mail_message
SET search_vector =
    setweight(to_tsvector('simple', coalesce(subject, '')), 'A') ||
    setweight(to_tsvector('simple', coalesce(from_address, '') || ' ' || coalesce(from_name, '')), 'B') ||
    setweight(to_tsvector('simple', coalesce(to_addresses::text, '') || ' ' || coalesce(cc_addresses::text, '')), 'B') ||
    setweight(to_tsvector('simple', coalesce(preview, '')), 'C');

CREATE INDEX IF NOT EXISTS mail_message_search_vector_gin ON mail_message USING GIN (search_vector);
CREATE INDEX IF NOT EXISTS mail_message_account_received_id_idx ON mail_message (account_id, date_received DESC NULLS LAST, id DESC);
'''

DROP_TRIGGER = r'''
DROP INDEX IF EXISTS mail_message_account_received_id_idx;
DROP INDEX IF EXISTS mail_message_search_vector_gin;
DROP TRIGGER IF EXISTS mail_message_search_vector_update ON mail_message;
DROP FUNCTION IF EXISTS mail_message_search_vector_update();
'''


def install_postgres_fts(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(CREATE_TRIGGER)


def uninstall_postgres_fts(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(DROP_TRIGGER)


class Migration(migrations.Migration):

    dependencies = [
        ('mail', '0008_mail7_uidvalidity_idempotency'),
    ]

    operations = [
        migrations.AddField(
            model_name='mailmessage',
            name='search_vector',
            field=django.contrib.postgres.search.SearchVectorField(blank=True, editable=False, null=True),
        ),
        migrations.RunPython(install_postgres_fts, reverse_code=uninstall_postgres_fts),
    ]
