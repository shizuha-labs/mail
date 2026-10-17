"""Standalone admin tool never selects an installation's private endpoint."""
import importlib.util
import os
from pathlib import Path
import sys
from unittest import mock, TestCase


class PortableStalwartAdminTests(TestCase):
    def load(self, endpoint=''):
        file = Path(__file__).resolve().parents[2] / 'scripts/stalwart_admin.py'
        spec = importlib.util.spec_from_file_location('portable_stalwart_admin',file)
        module = importlib.util.module_from_spec(spec)
        with mock.patch.dict(sys.modules,{'django':None}), mock.patch.dict(os.environ,{'STALWART_ADMIN_URL':endpoint}):
            spec.loader.exec_module(module)
        return module

    def test_missing_endpoint_refuses_before_constructing_authenticated_client(self):
        module = self.load()
        with mock.patch.object(module.httpx,'Client') as client:
            with self.assertRaisesRegex(RuntimeError,'STALWART_ADMIN_URL'):
                module.get_client()
        client.assert_not_called()

    def test_explicit_managed_endpoint_is_used_unchanged(self):
        module = self.load('http://mail-admin.example.invalid:8580')
        with module.get_client() as client:
            request = client.build_request('GET',module.STALWART_ADMIN_URL+'/api/principal')
            self.assertEqual(str(request.url),'http://mail-admin.example.invalid:8580/api/principal')

    def test_ambiguous_or_credential_bearing_endpoint_refuses(self):
        for value in ('file:///tmp/admin','http://user:pass@example.invalid','http://example.invalid/?query=value','http://example.invalid/#fragment'):
            with self.subTest(value=value):
                module = self.load(value)
                with self.assertRaises(RuntimeError):module.get_client()
