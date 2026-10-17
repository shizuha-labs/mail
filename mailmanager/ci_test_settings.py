"""MAIL-48: CI settings to run the authz-invariant / cross-owner isolation suite.

Inherits the real settings (so the JWT auth class, REST_FRAMEWORK config, URL
routing, and middleware exactly match production) and overrides ONLY the database
to in-memory sqlite with migrations disabled — the schema is built straight from
model state, so the gate needs no Postgres/Redis/Stalwart. This keeps the
cross-owner isolation tests exercising the SAME authz codepaths that ship.

Run: DJANGO_SETTINGS_MODULE=mailmanager.ci_test_settings \
     python manage.py test mail.tests.integration.test_mail48_cross_owner_authz mail.tests.integration.test_api
"""
import os
import secrets

# This isolated test module supplies a disposable key before real settings load.
os.environ.setdefault('SECRET_KEY', secrets.token_urlsafe(48))
os.environ.setdefault('POSTGRES_PASSWORD', secrets.token_urlsafe(32))
from .settings import *  # noqa: F401,F403,E402

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': ':memory:',
    }
}


class _DisableMigrations:
    def __contains__(self, item):
        return True

    def __getitem__(self, item):
        return None


MIGRATION_MODULES = _DisableMigrations()

# Deterministic signing key for create_test_jwt() ⇄ ShizuhaJWTAuthentication in CI.
# ≥32 bytes to satisfy HS256's minimum key length.
SECRET_KEY = 'mail48-authz-ci-secret-key-0123456789abcdef'
SIMPLE_JWT = {**globals().get('SIMPLE_JWT', {}), 'SIGNING_KEY': SECRET_KEY}

# Full-suite Celery tasks run synchronously and retain results in-process. Tests
# must never require a developer-local Redis just to observe an eager result.
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = False
CELERY_TASK_STORE_EAGER_RESULT = True
CELERY_RESULT_BACKEND = 'cache+memory://'
CELERY_BROKER_URL = 'memory://'
