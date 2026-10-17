"""Internal HTTP surface for Shizuha ID lifecycle delivery."""

from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .lifecycle import (
    LifecycleAuthenticationError,
    LifecyclePayloadError,
    consume_lifecycle_event,
    verify_signed_payload,
)


class UserLifecycleEventView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        try:
            payload = verify_signed_payload(body=request.body, headers=request.headers)
            receipt, processed = consume_lifecycle_event(payload)
        except LifecycleAuthenticationError:
            return Response({'error': 'unauthorized'}, status=401)
        except LifecyclePayloadError as exc:
            return Response({'error': str(exc)}, status=400)
        except Exception:
            return Response({'error': 'mailbox provisioning unavailable'}, status=503)
        return Response({
            'event_id': str(receipt.event_id),
            'status': 'processed' if processed else 'deduplicated',
        })
