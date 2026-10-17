import jwt as pyjwt
from django.conf import settings
from django.http import JsonResponse


class ServiceAccessMiddleware:
    SERVICE_SLUG = 'mail'

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not request.path.startswith('/api/'):
            return self.get_response(request)
        if '/internal/' in request.path:
            return self.get_response(request)

        auth_header = request.META.get('HTTP_AUTHORIZATION', '')
        if auth_header.startswith('Bearer '):
            token = auth_header[7:]
            try:
                payload = pyjwt.decode(
                    token,
                    getattr(settings, 'JWT_SECRET_KEY', settings.SECRET_KEY),
                    algorithms=['HS256'],
                    options={'verify_exp': True}
                )
                if payload.get('is_staff') or payload.get('is_superuser'):
                    return self.get_response(request)
                enabled_services = payload.get('enabled_services')
                if enabled_services is not None and self.SERVICE_SLUG not in enabled_services:
                    return JsonResponse(
                        {'error': 'You do not have access to this service.',
                         'service': self.SERVICE_SLUG,
                         'code': 'service_access_denied'},
                        status=403
                    )
            except Exception:
                pass  # Let DRF authentication handle invalid tokens

        return self.get_response(request)
