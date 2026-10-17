# MAIL-7: UIDVALIDITY tracking + UID-based idempotency on re-sync.
from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):

    dependencies = [
        ('mail', '0007_alter_mailmessage_unique_together_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='mailfolder',
            name='uidvalidity',
            field=models.BigIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='mailmessage',
            name='uidvalidity',
            field=models.BigIntegerField(blank=True, null=True),
        ),
        migrations.AddConstraint(
            model_name='mailmessage',
            constraint=models.UniqueConstraint(
                fields=['folder', 'uidvalidity', 'imap_uid'],
                condition=Q(uidvalidity__isnull=False) & ~Q(imap_uid=''),
                name='uniq_imap_msg_per_uidvalidity',
            ),
        ),
    ]
