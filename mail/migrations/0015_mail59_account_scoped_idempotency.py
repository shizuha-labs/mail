"""MAIL-59: Scope idempotency to account — replace global unique with account+client_message_id unique constraint.

Revision ID: 0015
Revises: 0014_mail59_outbox_message
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("mail", "0014_mail59_outbox_message"),
    ]

    operations = [
        # Remove the global unique constraint on client_message_id
        migrations.AlterField(
            model_name="outboxmessage",
            name="client_message_id",
            field=models.UUIDField(
                help_text="Client-generated idempotency key (unique per account)"
            ),
        ),
        # Add account-scoped unique constraint
        migrations.AddConstraint(
            model_name="outboxmessage",
            constraint=models.UniqueConstraint(
                fields=["account", "client_message_id"],
                name="mail_outbox_account_cmi_unique",
            ),
        ),
    ]
