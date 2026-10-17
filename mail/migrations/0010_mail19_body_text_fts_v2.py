# MAIL-19: body_text cache column + search_vector trigger v2 (weight D body FTS).
# Depends on 0009 (MAIL-17 trigger). Reversible back to the MAIL-17 trigger.
from django.db import migrations
import django.db.models.functions


# Trigger v2: adds body_text at weight D; keeps simple config; reverts cleanly.
TRIGGER_V2 = r'''
CREATE OR REPLACE FUNCTION mail_message_search_vector_update() RETURNS trigger AS $$
BEGIN
    NEW.search_vector :=
        setweight(to_tsvector('simple', coalesce(NEW.subject, '')), 'A') ||
        setweight(to_tsvector('simple', coalesce(NEW.from_address, '') || ' ' || coalesce(NEW.from_name, '')), 'B') ||
        setweight(to_tsvector('simple', coalesce(NEW.to_addresses::text, '') || ' ' || coalesce(NEW.cc_addresses::text, '')), 'B') ||
        setweight(to_tsvector('simple', coalesce(NEW.preview, '')), 'C') ||
        setweight(to_tsvector('simple', coalesce(NEW.body_text, '')), 'D');
    RETURN NEW;
END
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS mail_message_search_vector_update ON mail_message;
CREATE TRIGGER mail_message_search_vector_update
BEFORE INSERT OR UPDATE OF subject, from_address, from_name, to_addresses, cc_addresses, preview, body_text
ON mail_message
FOR EACH ROW EXECUTE FUNCTION mail_message_search_vector_update();
'''

# Revert to MAIL-17 trigger (no body_text, no weight D).
TRIGGER_V1 = r'''
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
'''


def apply_trigger_v2(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(TRIGGER_V2)


def revert_trigger_v2(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(TRIGGER_V1)


class Migration(migrations.Migration):

    dependencies = [
        ('mail', '0009_mailmessage_search_vector'),
    ]

    operations = [
        # 1. Add body_text column (nullable, additive — no lock on existing rows).
        migrations.AddField(
            model_name='mailmessage',
            name='body_text',
            field=django.db.models.TextField(null=True, blank=True),
        ),
        # 2. Add cap-marker column.
        migrations.AddField(
            model_name='mailmessage',
            name='body_text_truncated',
            field=django.db.models.BooleanField(default=False),
        ),
        # 3. Replace trigger with v2 (adds weight D body_text).
        migrations.RunPython(apply_trigger_v2, reverse_code=revert_trigger_v2),
    ]
