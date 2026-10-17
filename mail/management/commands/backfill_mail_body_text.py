"""Management command: invoke the body_text backfill synchronously (MAIL-32)."""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Backfill body_text for MailMessage rows that predate the MAIL-19 trigger.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--async',
            action='store_true',
            dest='run_async',
            help='Dispatch as a Celery task instead of running inline.',
        )

    def handle(self, *args, **options):
        from mail.tasks import backfill_message_body_text

        if options['run_async']:
            result = backfill_message_body_text.delay()
            self.stdout.write(f'Dispatched as Celery task: {result.id}')
        else:
            self.stdout.write('Running backfill inline...')
            result = backfill_message_body_text.apply()
            data = result.result or {}
            self.stdout.write(
                self.style.SUCCESS(
                    f"Done — processed={data.get('processed',0)} "
                    f"errors={data.get('errors',0)} "
                    f"total={data.get('total',0)}"
                )
            )
