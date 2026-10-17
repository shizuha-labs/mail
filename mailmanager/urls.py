"""
URL configuration for mailmanager project (Shizuha Mail).
"""
import os
import mimetypes
from django.contrib import admin
from django.urls import path, include, re_path
from django.conf import settings
from django.conf.urls.static import static
from django.http import FileResponse, Http404, JsonResponse
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularSwaggerView,
    SpectacularRedocView,
)


def health_check(request):
    """Simple health check endpoint for container health checks."""
    return JsonResponse({'status': 'healthy'})


def serve_media(request, path):
    """Serve media files - works in both DEBUG and production mode."""
    file_path = os.path.join(settings.MEDIA_ROOT, path)
    if os.path.exists(file_path) and os.path.isfile(file_path):
        content_type, _ = mimetypes.guess_type(file_path)
        return FileResponse(
            open(file_path, 'rb'),
            content_type=content_type or 'application/octet-stream'
        )
    raise Http404("File not found")

urlpatterns = [
    # Health check endpoint (for container healthchecks)
    path('api/health/', health_check, name='health-check'),

    # Prefixed URLs to match what nginx sends
    path('shizuha-mail/admin/', admin.site.urls),
    path('shizuha-mail/api/', include('mail.urls')),
    path('shizuha-mail/api/auth/', include('mail.auth_urls')),

    # OpenAPI schema and documentation (prefixed)
    path('shizuha-mail/api/schema/', SpectacularAPIView.as_view(), name='schema'),
    path('shizuha-mail/api/docs/', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),
    path('shizuha-mail/api/redoc/', SpectacularRedocView.as_view(url_name='schema'), name='redoc'),

    # Also keep non-prefixed URLs for direct access
    path('admin/', admin.site.urls),
    path('api/', include('mail.urls')),
    path('api/auth/', include('mail.auth_urls')),

    # OpenAPI schema and documentation (non-prefixed)
    path('api/schema/', SpectacularAPIView.as_view(), name='schema-direct'),
    path('api/docs/', SpectacularSwaggerView.as_view(url_name='schema-direct'), name='swagger-ui-direct'),
    path('api/redoc/', SpectacularRedocView.as_view(url_name='schema-direct'), name='redoc-direct'),
]

# Serve static files in development
if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)

# Serve media files (works in both DEBUG and production mode)
# MEDIA_URL is 'shizuha-mail/media/'
urlpatterns += [
    re_path(r'^shizuha-mail/media/(?P<path>.*)$', serve_media, name='serve_media'),
]
