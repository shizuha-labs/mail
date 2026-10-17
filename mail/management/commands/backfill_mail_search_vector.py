"""Management command: invoke the search_vector backfill synchronously (PLAT-6826)."""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Backfill search_vector for MailMessage rows where it is NULL (FTS search returns 0).'

    def add_arguments(self, parser):
        parser.add_argument(
            '--async',
            action='store_true',
            dest='run_async',
            help='Dispatch as a Celery task instead of running inline.',
        )

    def handle(self, *args, **options):
        from mail.tasks import backfill_message_search_vector

        if options['run_async']:
            result = backfill_message_search_vector.delay()
            self.stdout.write(f'Dispatched as Celery task: {result.id}')
        else:
            self.stdout.write('Running search_vector backfill inline...')
            result = backfill_message_search_vector.apply()
            data = result.result or {}
            self.stdout.write(
                self.style.SUCCESS(
                    f"Done — processed={data.get('processed',0)} "
                    f"errors={data.get('errors',0)} "
                    f"total={data.get('total',0)}"
                )
            )
