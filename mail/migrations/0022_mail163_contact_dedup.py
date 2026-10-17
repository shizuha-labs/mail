"""MAIL-163: normalize contact emails to lowercase + merge case-variant duplicates.

The model already enforces unique_together (account, email), but it is
case-sensitive — 'Friend@X.com' and 'friend@x.com' were distinct rows. This
migration normalizes stored emails to lowercase (matching the canonical_email
rule in services/contact_service.py) and merges any rows that collide after
normalization deterministically (keep highest send+receive counts, sum signals).
"""

from django.db import migrations


def _normalize_and_merge(apps, schema_editor):
    MailContact = apps.get_model("mail", "MailContact")
    from itertools import groupby
    from django.db.models.functions import Lower, Trim

    database = schema_editor.connection.alias if schema_editor else "default"
    contacts = MailContact.objects.using(database)
    ordered = contacts.annotate(canonical_email=Lower(Trim("email"))).order_by(
        "account_id", "canonical_email", "-send_count", "-receive_count",
        "-last_contacted_at", "created_at", "pk",
    )
    # Group by the destination address before changing any row. Renaming first
    # violates the existing unique(account, email) constraint for case variants.
    for (_, email), group in groupby(
        ordered.iterator(), key=lambda contact: (contact.account_id, contact.canonical_email)
    ):
        rows = list(group)
        keep = rows[0]
        keep.send_count = sum(row.send_count for row in rows)
        keep.receive_count = sum(row.receive_count for row in rows)
        keep.last_contacted_at = max(
            (row.last_contacted_at for row in rows if row.last_contacted_at), default=None
        )
        keep.name = next((row.name for row in rows if row.name), "")
        contacts.filter(pk__in=[row.pk for row in rows[1:]]).delete()
        keep.email = email
        keep.save(using=database)


class Migration(migrations.Migration):

    dependencies = [
        ("mail", "0021_mail66_delivery_state"),
    ]

    operations = [
        migrations.RunPython(_normalize_and_merge, migrations.RunPython.noop),
    ]
