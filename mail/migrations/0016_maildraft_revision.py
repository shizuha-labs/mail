from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('mail', '0015_mail59_account_scoped_idempotency')]
    operations = [
        migrations.AddField(
            model_name='maildraft',
            name='revision',
            field=models.PositiveBigIntegerField(default=1),
        ),
    ]
