"""Explicit online ID authority for Mail's existing object-authorization tests.

The real JWT signature and Mail authentication code remain active. Revoked and
unavailable authority cases are exercised by test_revocation_transport.
"""
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from django.test import TestCase
from mail import token_revocation


class ActiveIdentityTestCase(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        class Authority(BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers['Content-Length']))
                body = json.dumps({'active': True}).encode()
                self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Authority)
        worker = threading.Thread(target=lambda: server.serve_forever(poll_interval=.05), daemon=True)
        worker.start()
        def stop():
            server.shutdown()
            server.server_close()
            worker.join(timeout=3)
            token_revocation._REVOCATION_CACHE.clear()
        cls.addClassCleanup(stop)
        env = patch.dict(os.environ, {'SHIZUHA_REVOCATION_STATUS_URL':
                                     f'http://127.0.0.1:{server.server_port}/status'})
        env.start()
        cls.addClassCleanup(env.stop)
        token_revocation._REVOCATION_CACHE.clear()
