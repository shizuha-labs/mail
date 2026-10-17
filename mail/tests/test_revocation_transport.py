"""Exercise real HTTP revocation transport through the JWT authentication caller."""
import json
import os
import threading
import time
import uuid
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from django.test import SimpleTestCase
from rest_framework_simplejwt.tokens import AccessToken
from rest_framework_simplejwt.exceptions import InvalidToken

from mail.authentication import ShizuhaJWTAuthentication
from mail import authentication as auth_module
from mail import token_revocation as revocation


class RevocationTransportTests(SimpleTestCase):
    def setUp(self):
        self.responses = []
        self.calls = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                owner.calls.append((self.path, json.loads(self.rfile.read(int(self.headers['Content-Length'])))))
                status, body = owner.responses.pop(0)
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}/api/oauth/revocation-status'
        self.env = patch.dict(os.environ, {
            'SHIZUHA_REVOCATION_STATUS_URL': self.url,
            'SHIZUHA_EXPECTED_ISSUER': 'https://identity.example/id',
        })
        self.env.start()
        revocation._REVOCATION_CACHE.clear()
        token = AccessToken()
        token['client_id'] = 'transport-test'
        self.raw = str(token).encode()
        self.auth = ShizuhaJWTAuthentication()

    def tearDown(self):
        self.env.stop()
        self.server.shutdown()
        self.server.server_close()
        self.worker.join(timeout=3)
        revocation._REVOCATION_CACHE.clear()

    def test_unavailable_lookup_denies_but_next_request_recovers(self):
        self.responses.extend([(503, {}), (200, {'active': True})])
        with self.assertRaises(InvalidToken):
            self.auth.get_validated_token(self.raw)
        self.assertNotIn(revocation._cache_key(self.raw.decode()), revocation._REVOCATION_CACHE)
        self.auth.get_validated_token(self.raw)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.calls[0], ('/api/oauth/revocation-status', {'token': self.raw.decode()}))
        self.assertEqual(os.environ['SHIZUHA_EXPECTED_ISSUER'], 'https://identity.example/id')

    def test_real_revocation_still_denies_and_is_cached(self):
        self.responses.extend([(200, {'active': True}), (200, {'active': False})])
        self.auth.get_validated_token(self.raw)
        revocation._REVOCATION_CACHE[revocation._cache_key(self.raw.decode())] = (time.monotonic()-1, False)
        for _ in range(2):
            with self.assertRaises(InvalidToken):
                self.auth.get_validated_token(self.raw)
        self.assertEqual(len(self.calls), 2)

    def test_malformed_active_never_authorizes_or_poisons_cache(self):
        self.responses.extend([(200, []), (200, {'active': 'false'}), (200, {'active': True})])
        for _ in range(2):
            with self.assertRaises(InvalidToken):
                self.auth.get_validated_token(self.raw)
            self.assertFalse(revocation._REVOCATION_CACHE)
        self.auth.get_validated_token(self.raw)

    def test_legacy_issuer_url_remains_compatible_when_unconfigured(self):
        with patch.dict(os.environ, {'SHIZUHA_REVOCATION_STATUS_URL': ''}):
            self.assertEqual(revocation._revocation_status_url(), 'https://identity.example/id/api/oauth/revocation-status')


class SessionRevocationTests(RevocationTransportTests):
    """Actual RS256 caller and HTTP verdict; no OAuth client_id shortcut."""
    def setUp(self):
        super().setUp()
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.old_jwks = dict(auth_module._JWKS_CACHE)
        auth_module._JWKS_CACHE.update(keys={'session-test': self.key.public_key()}, fetched_at=time.time())

    def tearDown(self):
        auth_module._JWKS_CACHE.clear()
        auth_module._JWKS_CACHE.update(self.old_jwks)
        super().tearDown()

    def session_token(self, version):
        return jwt.encode({'user_id': 1, 'sub': '1', 'token_type': 'access',
                           'jti': str(uuid.uuid4()), 'exp': int(time.time())+60,
                           'token_version': version}, self.key, algorithm='RS256',
                          headers={'kid': 'session-test'}).encode()

    def test_session_active_stale_active_uses_authoritative_version(self):
        self.responses.extend([(200, {'active': True, 'token_version': 3})]*3)
        self.auth.get_validated_token(self.session_token(3))
        with self.assertRaises(InvalidToken):
            self.auth.get_validated_token(self.session_token(2))
        self.auth.get_validated_token(self.session_token(3))
        self.assertEqual(len(self.calls), 3)
        self.assertTrue(all('client_id' not in jwt.decode(call[1]['token'], options={'verify_signature': False}) for call in self.calls))

    def test_session_transport_failure_denies_uncached_then_recovers(self):
        raw = self.session_token(3)
        self.responses.extend([(503, {}), (200, {'active': True, 'token_version': 3})])
        with self.assertRaises(InvalidToken):
            self.auth.get_validated_token(raw)
        self.assertFalse(revocation._REVOCATION_CACHE)
        self.auth.get_validated_token(raw)
        self.assertEqual(len(self.calls), 2)

    def test_session_authoritatively_revoked_is_cached_denial(self):
        raw = self.session_token(3)
        self.responses.append((200, {'active': False, 'token_version': 3}))
        for _ in range(2):
            with self.assertRaises(InvalidToken):
                self.auth.get_validated_token(raw)
        self.assertEqual(len(self.calls), 1)

    def test_malformed_present_version_denies_uncached(self):
        raw = self.session_token(3)
        self.responses.extend([(200, {'active': True, 'token_version': '3'}),
                               (200, {'active': True, 'token_version': True}),
                               (200, {'active': True, 'token_version': 3})])
        for _ in range(2):
            with self.assertRaises(InvalidToken):
                self.auth.get_validated_token(raw)
            self.assertFalse(revocation._REVOCATION_CACHE)
        self.auth.get_validated_token(raw)
