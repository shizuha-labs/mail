from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('mail', '0017_userlifecycleeventreceipt'),
    ]

    operations = [
        migrations.AddField(
            model_name='outboxmessage',
            name='provider_started',
            field=models.BooleanField(
                default=None,
                help_text=(
                    'Typed MAIL-59 boundary: false only when code proved provider I/O '
                    'had not started; true once submission may be ambiguous; null for legacy/unknown.'
                ),
                null=True,
            ),
        ),
    ]
