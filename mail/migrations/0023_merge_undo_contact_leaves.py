"""Join independently landed undo-log and contact-dedup migrations."""
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ('mail', '0022_mail162_undo_log'),
        ('mail', '0022_mail163_contact_dedup'),
    ]
    operations = []
