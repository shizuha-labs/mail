"""MAIL-58: /api/internal/ endpoints must be unreachable via the public ingress.

The prior guard (presence of any X-Internal-Service header) was trivially
bypassable. InternalApiIngressGuardMiddleware 404s any /api/internal/* request
that arrived through the ingress (detected by X-Forwarded-For/X-Real-IP), even
if it carries a spoofed X-Internal-Service header; direct in-cluster calls
(no forwarded headers) pass through. Tested at the middleware level for
determinism (no URL/view/DB dependency).
"""
from django.conf import settings
from django.test import RequestFactory, SimpleTestCase

from mail.internal_api_guard import (
    InternalApiIngressGuardMiddleware,
    request_via_ingress,
)

_SENTINEL = object()


class InternalIngressGuardTests(SimpleTestCase):
    def setUp(self):
        self.rf = RequestFactory()
        self.mw = InternalApiIngressGuardMiddleware(lambda req: _SENTINEL)

    def test_ingress_borne_internal_is_404_even_with_spoofed_header(self):
        req = self.rf.post('/api/internal/send/', HTTP_X_FORWARDED_FOR='203.0.113.7',
                           HTTP_X_INTERNAL_SERVICE='anything')
        resp = self.mw(req)
        self.assertEqual(resp.status_code, 404)  # the exact bypass from the finding, now blocked

    def test_x_real_ip_also_blocks(self):
        req = self.rf.post('/api/internal/send/', HTTP_X_REAL_IP='203.0.113.7')
        self.assertEqual(self.mw(req).status_code, 404)

    def test_prefixed_ingress_path_also_blocks(self):
        req = self.rf.post('/shizuha-mail/api/internal/send/', HTTP_X_FORWARDED_FOR='203.0.113.7')
        self.assertEqual(self.mw(req).status_code, 404)

    def test_direct_in_cluster_internal_passes_through(self):
        req = self.rf.post('/api/internal/send/')  # no X-Forwarded-For / X-Real-IP
        self.assertIs(self.mw(req), _SENTINEL)

    def test_non_internal_path_passes_even_via_ingress(self):
        req = self.rf.get('/api/health/', HTTP_X_FORWARDED_FOR='203.0.113.7')
        self.assertIs(self.mw(req), _SENTINEL)

    def test_request_via_ingress_helper(self):
        self.assertTrue(request_via_ingress(self.rf.get('/x', HTTP_X_FORWARDED_FOR='1.2.3.4')))
        self.assertTrue(request_via_ingress(self.rf.get('/x', HTTP_X_REAL_IP='1.2.3.4')))
        self.assertFalse(request_via_ingress(self.rf.get('/x')))

    def test_guard_is_wired_in_middleware(self):
        self.assertIn('mail.internal_api_guard.InternalApiIngressGuardMiddleware', settings.MIDDLEWARE)
