"""
URL configuration for Shizuha Mail API.
"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    MailAccountViewSet, MailFolderViewSet, MailMessageViewSet,
    MailThreadViewSet, MailDraftViewSet, MailSendViewSet,
    MailFilterViewSet, MailContactViewSet, InternalSendView, InternalDeliveryEventView,
    EmailProviderViewSet, MailLabelViewSet, MailAttachmentDownloadView
)
from .oauth_account_views import OAuthAccountInitiateView, OAuthAccountCallbackView
from .lifecycle_views import UserLifecycleEventView

router = DefaultRouter()
router.register(r'accounts', MailAccountViewSet, basename='accounts')
router.register(r'providers', EmailProviderViewSet, basename='providers')
router.register(r'folders', MailFolderViewSet, basename='folders')
router.register(r'labels', MailLabelViewSet, basename='labels')
router.register(r'messages', MailMessageViewSet, basename='messages')
router.register(r'threads', MailThreadViewSet, basename='threads')
router.register(r'drafts', MailDraftViewSet, basename='drafts')
router.register(r'send', MailSendViewSet, basename='send')
router.register(r'filters', MailFilterViewSet, basename='filters')
router.register(r'contacts', MailContactViewSet, basename='contacts')

urlpatterns = [
    path('', include(router.urls)),
    # KOT-88: owner-scoped JMAP attachment blob proxy
    path(
        'attachments/<str:blob_id>/download/',
        MailAttachmentDownloadView.as_view(),
        name='mail-attachment-download',
    ),
    # OAuth account linking — MAIL-41
    path('accounts/oauth/initiate/', OAuthAccountInitiateView.as_view(), name='oauth-account-initiate'),
    path('accounts/oauth/callback/', OAuthAccountCallbackView.as_view(), name='oauth-account-callback'),
    # Internal endpoint for system emails (password reset, notifications, etc.)
    path('internal/send/', InternalSendView.as_view(), name='internal-send'),
    path(
        'internal/user-lifecycle-events/',
        UserLifecycleEventView.as_view(),
        name='internal-user-lifecycle-events',
    ),
    path('internal/delivery-events/', InternalDeliveryEventView.as_view(), name='internal-delivery-events'),
]
