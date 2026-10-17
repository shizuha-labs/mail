# Explicit installation credentials

Set `JWT_SECRET_KEY` or `SECRET_KEY` and `POSTGRES_PASSWORD` before starting the
service, including when `DEBUG` is enabled. Empty or whitespace-only values are
refused; a present JWT key takes precedence, and a blank selected key is refused. Static image collection supplies
its own disposable inline build configuration, and isolated SQLite test settings
use disposable test configuration rather than production credential defaults.

The complete regression profile uses a disposable PostgreSQL 15 instance and
production migrations: set explicit `POSTGRES_*` connection values, then run
`python manage.py test mail --settings=mailmanager.pg_test_settings --noinput`.
The small `ci_test_settings` authorization lane remains isolated on SQLite; it
is not a substitute for the complete PostgreSQL historical/FTS regressions.
