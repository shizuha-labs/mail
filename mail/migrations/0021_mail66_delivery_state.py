from django.db import migrations, models


class Migration(migrations.Migration):
    # MAIL-66 merge: main advanced 0017->0020 while this branch was open, so the
    # original 0016 parent made this a SIBLING of 0017_userlifecycleeventreceipt
    # and left the graph with two leaves — makemigrations --check would fail and
    # a deploy would hit InconsistentMigrationHistory. Re-parented onto main's
    # current leaf and renumbered; the AddFields are unchanged and additive, so
    # ordering after main's fields is safe.
    dependencies = [('mail', '0020_disable_superseded_domain_accounts')]
    operations = [
        migrations.AddField(
            model_name='outboxmessage', name='delivery_status',
            field=models.CharField(
                choices=[('pending', 'Pending'), ('delayed', 'Delayed'),
                         ('delivered', 'Delivered'), ('failed', 'Failed')],
                default='pending', max_length=10,
            ),
        ),
        migrations.AddField(
            model_name='outboxmessage', name='delivery_diagnostic',
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name='outboxmessage', name='delivery_updated_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
