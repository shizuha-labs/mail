"""Exercise actual settings imports with fresh process environments."""
import os
from pathlib import Path
import secrets
import subprocess
import sys
from unittest import TestCase


class RequiredSigningKeyTest(TestCase):
    def load(self, **settings):
        env = {key: value for key, value in os.environ.items()
               if key not in {'JWT_SECRET_KEY', 'SECRET_KEY', 'DJANGO_SETTINGS_MODULE'}}
        env['POSTGRES_PASSWORD'] = secrets.token_urlsafe(32)
        env.update(settings)
        return subprocess.run([sys.executable, '-c',
            'import os; from mailmanager.settings import SECRET_KEY; '
            'assert SECRET_KEY == os.environ["EXPECTED_KEY"]'],
            cwd=Path(__file__).resolve().parents[3], env=env,
            capture_output=True, text=True)

    def test_missing_or_empty_keys_fail_even_in_debug(self):
        for values in ({}, {'JWT_SECRET_KEY': '', 'SECRET_KEY': ''}):
            result = self.load(DEBUG='True', **values)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('Set JWT_SECRET_KEY or SECRET_KEY', result.stderr)

    def test_explicit_jwt_key_keeps_precedence(self):
        primary, fallback = secrets.token_urlsafe(48), secrets.token_urlsafe(48)
        result = self.load(JWT_SECRET_KEY=primary, SECRET_KEY=fallback, EXPECTED_KEY=primary)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_explicit_django_key_is_supported(self):
        key = secrets.token_urlsafe(48)
        result = self.load(SECRET_KEY=key, EXPECTED_KEY=key)
        self.assertEqual(result.returncode, 0, result.stderr)
