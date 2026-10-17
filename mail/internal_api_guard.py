"""Internal-API ingress guard (MAIL-58; CON-208 / shizuha-id pattern).

shizuha-mail's ``/api/internal/`` endpoints (e.g. ``/api/internal/send/``,
``InternalSendView``) are service-to-service APIs meant to be reachable ONLY on
the cluster network, but they are also publicly mounted through the shizuha-mail
ingress. The prior guard — a *presence* check on ``X-Internal-Service`` — was
trivially bypassable: any non-empty header value (even ``"x"``) passed, so an
external client could send arbitrary email from the platform identity.

This middleware closes the actual hole: any request to an internal path that
arrived THROUGH the public ingress is rejected with 404. Detection: the nginx
ingress always appends ``X-Forwarded-For`` / ``X-Real-IP`` to proxied requests
and an external client cannot make it omit them, while direct in-cluster Service
calls carry neither. An in-cluster caller that spoofs the header simply locks
itself out — fail closed. 404 (not 403) so the internal path is not advertised
as publicly existing.

Ref: shizuha-id ``identity/internal_api_guard.py``; CON-208 (Connect, same root
cause); parent sweep PLAT-4015.
"""

from django.http import JsonResponse

# Substring match catches both mounts: `/api/internal/...` and the ingress-
# prefixed `/shizuha-mail/api/internal/...`.
_INTERNAL_MARKER = '/api/internal/'


def request_via_ingress(request) -> bool:
    """True when the request was proxied by the public ingress."""
    meta = request.META
    return bool(meta.get('HTTP_X_FORWARDED_FOR') or meta.get('HTTP_X_REAL_IP'))


class InternalApiIngressGuardMiddleware:
    """404 any ingress-borne request to the internal service-to-service API."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if _INTERNAL_MARKER in request.path and request_via_ingress(request):
            return JsonResponse({'error': 'Not found'}, status=404)
        return self.get_response(request)
