"""Execute the shipped container entrypoint without a database or network."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


class SafeEntrypointTest(unittest.TestCase):
    def test_startup_never_creates_an_implicit_administrator(self):
        root = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            log = directory / 'calls'
            for name in ('python', 'nc', 'pip', 'supervisord'):
                file = directory / name
                file.write_text('#!/bin/sh\nprintf "%s %s %s\\n" "$(basename "$0")" "$1" "$2" >> "$ENTRYPOINT_CALLS"\n')
                file.chmod(0o755)
            env = dict(os.environ, PATH=str(directory) + os.pathsep + os.environ['PATH'], ENTRYPOINT_CALLS=str(log))
            run = subprocess.run(['bash', str(root / 'docker-entrypoint.sh'), 'supervisord'],
                                 cwd=directory, env=env, capture_output=True, text=True, timeout=30)
            self.assertEqual(run.returncode, 0)
            calls = log.read_text().splitlines()
            management = [line for line in calls if line.startswith('python manage.py ')]
            self.assertEqual(management, ['python manage.py createdb', 'python manage.py makemigrations', 'python manage.py migrate', 'python manage.py collectstatic'])
            self.assertTrue(any(line.startswith('supervisord ') for line in calls))
