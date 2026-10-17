"""DRF exception handling with Mail's secret redaction boundary."""
from rest_framework.views import exception_handler

from .redaction import redact_sensitive


def redacting_exception_handler(exc, context):
    response = exception_handler(exc, context)
    if response is not None and getattr(response, 'data', None) is not None:
        response.data = redact_sensitive(response.data)
    return response
