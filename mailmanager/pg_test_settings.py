"""Complete Mail regressions with real PostgreSQL and production migrations.

Retains the isolated CI JWT/Celery fixtures. POSTGRES_* must describe a disposable
PostgreSQL test instance; Django creates and destroys only its test database.
"""
from .ci_test_settings import *  # noqa: F401,F403
from .settings import DATABASES as _PRODUCTION_DATABASES

DATABASES = _PRODUCTION_DATABASES
MIGRATION_MODULES = {}
