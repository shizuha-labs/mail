"""
Django settings for mailmanager project (Shizuha Mail).
"""

import os
from pathlib import Path
from datetime import timedelta
from django.core.exceptions import ImproperlyConfigured


def _required_env(name):
    value = os.environ.get(name)
    if not value or not value.strip():
        raise ImproperlyConfigured(f'{name} must be configured')
    return value

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# SECURITY WARNING: keep the secret key used in production secret!
# Use JWT_SECRET_KEY if available (shared with shizuha-id), fallback to SECRET_KEY
SECRET_KEY = os.environ['JWT_SECRET_KEY'] if 'JWT_SECRET_KEY' in os.environ else os.environ.get('SECRET_KEY')
if not SECRET_KEY or not SECRET_KEY.strip():
    raise ImproperlyConfigured('Set JWT_SECRET_KEY or SECRET_KEY before starting Mail')

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = os.environ.get('DEBUG', 'True').lower() == 'true'

ALLOWED_HOSTS = [value.strip() for value in os.environ.get('ALLOWED_HOSTS', 'localhost,127.0.0.1,0.0.0.0,host.docker.internal,shizuha-nginx,*').split(',') if value.strip()]

# Set to False to prevent Django from redirecting URLs without trailing slashes
APPEND_SLASH = False

FORCE_SCRIPT_NAME = os.environ.get('FORCE_SCRIPT_NAME', '/mail')
LOGIN_URL = '/admin/login/'
LOGIN_REDIRECT_URL = '/admin/'

USE_X_FORWARDED_HOST = True
USE_X_FORWARDED_PORT = True

# Additional settings to handle reverse proxy properly
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

# Disable problematic security features that interfere with Django admin
SECURE_CONTENT_TYPE_NOSNIFF = False
SECURE_CROSS_ORIGIN_OPENER_POLICY = None

# Application definition
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.postgres',
    'django.contrib.sites',
    'rest_framework',
    'rest_framework_simplejwt',
    'rest_framework_simplejwt.token_blacklist',
    'drf_spectacular',
    'corsheaders',
    'django_celery_beat',
    # Allauth
    'allauth',
    'allauth.account',
    'allauth.socialaccount',
    # dj-rest-auth
    'dj_rest_auth',
    'dj_rest_auth.registration',
    # App
    'mail.apps.MailConfig',
]

SITE_ID = 1

MIDDLEWARE = [
    'corsheaders.middleware.CorsMiddleware',
    'mailmanager.middleware.ScriptNameMiddleware',
    # MAIL-58: 404 any ingress-borne request to /api/internal/* (fail-closed).
    'mail.internal_api_guard.InternalApiIngressGuardMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'mail.middleware.ServiceAccessMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'allauth.account.middleware.AccountMiddleware',
]

ROOT_URLCONF = 'mailmanager.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'mailmanager.wsgi.application'


# Database
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.environ.get('POSTGRES_DB', 'shizuha_mail'),
        'USER': os.environ.get('POSTGRES_USER', 'postgres'),
        'PASSWORD': _required_env('POSTGRES_PASSWORD'),
        'HOST': os.environ.get('POSTGRES_HOST', 'localhost'),
        'PORT': os.environ.get('POSTGRES_PORT', '5432'),
    }
}

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]


# Internationalization
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'Asia/Kolkata'
USE_I18N = True
USE_TZ = True


# Static files (CSS, JavaScript, Images)
STATIC_URL = 'shizuha-mail/static/'
STATIC_ROOT = os.path.join(BASE_DIR, 'staticfiles')

# Media files
MEDIA_URL = 'shizuha-mail/media/'
MEDIA_ROOT = os.path.join(BASE_DIR, 'media')

# Maximum upload size (25MB for attachments)
MAX_UPLOAD_SIZE = int(os.environ.get('MAX_ATTACHMENT_SIZE', 26214400))
DATA_UPLOAD_MAX_MEMORY_SIZE = MAX_UPLOAD_SIZE
FILE_UPLOAD_MAX_MEMORY_SIZE = MAX_UPLOAD_SIZE

# Default primary key field type
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'


# Celery Configuration
CELERY_BROKER_URL = os.environ.get('CELERY_BROKER_URL', 'redis://:@localhost:6379/5')
CELERY_RESULT_BACKEND = os.environ.get('CELERY_RESULT_BACKEND', 'redis://:@localhost:6379/5')
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_TIMEZONE = 'Asia/Kolkata'
CELERY_ENABLE_UTC = True

# Task routing configuration for isolation
CELERY_TASK_DEFAULT_QUEUE = 'shizuha_mail'
CELERY_TASK_DEFAULT_EXCHANGE = 'shizuha_mail'
CELERY_TASK_DEFAULT_ROUTING_KEY = 'shizuha_mail'

# Redis connection retry settings
CELERY_BROKER_CONNECTION_RETRY = True
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
CELERY_BROKER_CONNECTION_MAX_RETRIES = 10
CELERY_BROKER_TRANSPORT_OPTIONS = {
    'visibility_timeout': 3600,
    'socket_timeout': 30,
    'socket_connect_timeout': 30,
    'socket_keepalive': True,
    'retry_on_timeout': True
}

# Periodic task schedule (MAIL-34)
CELERY_BEAT_SCHEDULE = {
    'mail-periodic-sync': {
        'task': 'mail.tasks.periodic_sync_all_accounts',
        'schedule': float(os.environ.get('MAIL_PERIODIC_SYNC_INTERVAL', 300)),
        'options': {'queue': 'shizuha_mail'},
    },
    # MAIL-107: daily converge of managed-identity routing + synthetic-fixture
    # TTL expiry. Without this entry the reconcile command had zero production
    # callers and expired fixtures stayed active in the Zen production selector.
    'mail-managed-reconcile': {
        'task': 'mail.tasks.reconcile_managed_mail_accounts_periodic',
        'schedule': float(os.environ.get('MAIL_RECONCILE_INTERVAL', 86400)),
        'options': {'queue': 'shizuha_mail'},
    },
}


# REST Framework settings
REST_FRAMEWORK = {
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    'PAGE_SIZE': 50,
    'DEFAULT_SCHEMA_CLASS': 'drf_spectacular.openapi.AutoSchema',
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'mail.authentication.ShizuhaJWTAuthentication',
        'rest_framework.authentication.SessionAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
        'mail.permissions.ServiceAccessPermission',
    ],
    'EXCEPTION_HANDLER': 'mail.exceptions.redacting_exception_handler',
}

# Account-scoped rolling send limits. Durable outbox admission enforces both.
MAIL_SEND_QUOTA = {
    'per_minute': int(os.environ.get('MAIL_SEND_QUOTA_PER_MINUTE', '5')),
    'per_day': int(os.environ.get('MAIL_SEND_QUOTA_PER_DAY', '50')),
}

# MAIL-157: cap total envelope recipients (to+cc+bcc) per admitted send so one
# request can never amplify past a bounded fan-out while consuming one quota
# unit. The v1 agent surface additionally enforces exactly one recipient.
MAIL_MAX_RECIPIENTS_PER_SEND = int(
    os.environ.get('MAIL_MAX_RECIPIENTS_PER_SEND', '50')
)

# JWT Settings - Must match shizuha-id settings for token validation
SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(hours=24),  # Match shizuha-id
    'REFRESH_TOKEN_LIFETIME': timedelta(days=30),  # Match shizuha-id
    'ROTATE_REFRESH_TOKENS': True,
    'BLACKLIST_AFTER_ROTATION': True,
    'UPDATE_LAST_LOGIN': True,
    'ALGORITHM': 'HS256',
    'SIGNING_KEY': SECRET_KEY,
    'AUTH_HEADER_TYPES': ('Bearer',),
    'AUTH_HEADER_NAME': 'HTTP_AUTHORIZATION',
    'USER_ID_FIELD': 'id',
    'USER_ID_CLAIM': 'user_id',
    'AUTH_TOKEN_CLASSES': ('rest_framework_simplejwt.tokens.AccessToken',),
    'TOKEN_TYPE_CLAIM': 'token_type',
}

# dj-rest-auth settings
REST_AUTH = {
    'USE_JWT': True,
    'JWT_AUTH_COOKIE': 'shizuha-access-token',
    'JWT_AUTH_REFRESH_COOKIE': 'shizuha-refresh-token',
    'JWT_AUTH_HTTPONLY': True,
    'JWT_AUTH_SECURE': not DEBUG,
    'JWT_AUTH_SAMESITE': 'Lax',
    'SESSION_LOGIN': False,
    'TOKEN_MODEL': None,
}

# Allauth settings
ACCOUNT_LOGIN_METHODS = {'email', 'username'}
ACCOUNT_SIGNUP_FIELDS = ['email*', 'username*', 'password1*', 'password2*']
ACCOUNT_EMAIL_VERIFICATION = 'optional'
ACCOUNT_UNIQUE_EMAIL = True

# Authentication backends
AUTHENTICATION_BACKENDS = [
    'django.contrib.auth.backends.ModelBackend',
    'allauth.account.auth_backends.AuthenticationBackend',
]

# DRF Spectacular settings for OpenAPI documentation
SPECTACULAR_SETTINGS = {
    'TITLE': 'Shizuha Mail API',
    'DESCRIPTION': '''
Email service API for Shizuha platform.

## Overview

Shizuha Mail provides a REST API for managing:
- **Mail Accounts**: User email accounts provisioned in Stalwart
- **Mailboxes/Folders**: Inbox, Sent, Drafts, Trash, custom folders
- **Messages**: Email messages with full MIME support
- **Threads**: Conversation threading
- **Attachments**: Email attachments

## Authentication

Uses JWT tokens issued by Shizuha ID.
    ''',
    'VERSION': '1.0.0',
    'SERVE_INCLUDE_SCHEMA': False,
    'COMPONENT_SPLIT_REQUEST': True,
    'SCHEMA_PATH_PREFIX': r'/api/',
}

# CORS settings
CORS_ALLOW_ALL_ORIGINS = os.environ.get('CORS_ALLOW_ALL_ORIGINS', 'true').lower() in ('true', '1', 'yes')
CORS_ALLOWED_ORIGINS = [value.strip() for value in os.environ.get('CORS_ALLOWED_ORIGINS', '').split(',') if value.strip()]

# CSRF settings for proxy setup
CSRF_TRUSTED_ORIGINS = [value.strip() for value in os.environ.get('CSRF_TRUSTED_ORIGINS', 'http://localhost:8013,http://127.0.0.1:8013,http://shizuha-nginx,https://shizuha.com,https://mail.shizuha.com,https://mail-shadow.shizuha.com').split(',') if value.strip()]

# Server Port Settings
SERVER_PORT = os.environ.get('SERVER_PORT', '8013')

# Shizuha-ID Service Integration
SHIZUHA_ID_API_URL = os.environ.get('SHIZUHA_ID_API_URL', 'http://shizuha-id:8001/api')

# MAIL-74: request-scoped delegated token exchange at Shizuha ID for managed
# Stalwart accounts. Never persist the resulting access token.
SHIZUHA_OAUTH_ISSUER = os.environ.get(
    'SHIZUHA_OAUTH_ISSUER',
    os.environ.get('SHIZUHA_ID_API_URL', 'http://shizuha-id:8001').rstrip('/').removesuffix('/api'),
)
SHIZUHA_OAUTH_TOKEN_URL = os.environ.get(
    'SHIZUHA_OAUTH_TOKEN_URL',
    f"{SHIZUHA_OAUTH_ISSUER.rstrip('/')}/api/oauth/token",
)
MAIL_DELEGATED_TOKEN_EXCHANGE_ENABLED = os.environ.get(
    'MAIL_DELEGATED_TOKEN_EXCHANGE_ENABLED', 'true'
).lower() in ('1', 'true', 'yes')

# Stalwart Mail Server Integration
STALWART_JMAP_URL = os.environ.get('STALWART_JMAP_URL', 'http://stalwart-mail:8080')
STALWART_ADMIN_URL = os.environ.get('STALWART_ADMIN_URL', 'http://stalwart-mail:8080')
# Admin auth - supports both Basic Auth (user/password) and Bearer token
STALWART_ADMIN_USER = os.environ.get('STALWART_ADMIN_USER', 'admin')
STALWART_ADMIN_PASSWORD = os.environ.get('STALWART_ADMIN_PASSWORD', '')
STALWART_ADMIN_TOKEN = os.environ.get('STALWART_ADMIN_TOKEN', '')
# SSL verification - set to 'false' to disable for self-signed certs or IP-based access
STALWART_VERIFY_SSL = os.environ.get('STALWART_VERIFY_SSL', 'true').lower() != 'false'
STALWART_PROVISIONING_URL = os.environ.get('STALWART_PROVISIONING_URL', STALWART_ADMIN_URL)
STALWART_PROVISIONING_USER = os.environ.get('STALWART_PROVISIONING_USER', '')
STALWART_PROVISIONING_PASSWORD = os.environ.get('STALWART_PROVISIONING_PASSWORD', '')
STALWART_PROVISIONING_TIMEOUT = int(os.environ.get('STALWART_PROVISIONING_TIMEOUT', '10'))
STALWART_PROVISIONING_VERIFY_SSL = os.environ.get(
    'STALWART_PROVISIONING_VERIFY_SSL', 'true'
).lower() != 'false'
# ID-managed mailboxes can converge on a shadow plane while legacy accounts
# continue using STALWART_JMAP_URL until the public cutover is approved.
UNIFIED_MAIL_JMAP_URL = os.environ.get('UNIFIED_MAIL_JMAP_URL', STALWART_JMAP_URL)
UNIFIED_MAIL_SMTP_HOST = os.environ.get(
    'UNIFIED_MAIL_SMTP_HOST', 'stalwart-shadow-mail.mail-shadow.svc.cluster.local'
)
UNIFIED_MAIL_SMTP_PORT = int(os.environ.get('UNIFIED_MAIL_SMTP_PORT', '587'))
ID_TO_MAIL_LIFECYCLE_SECRET = os.environ.get('ID_TO_MAIL_LIFECYCLE_SECRET', '')
USER_LIFECYCLE_SIGNATURE_MAX_AGE_SECONDS = int(
    os.environ.get('USER_LIFECYCLE_SIGNATURE_MAX_AGE_SECONDS', '300')
)
MAIL_DOMAIN = os.environ.get('MAIL_DOMAIN', 'shizuha.com')

# Credential Encryption
# Required for storing email passwords securely
# Generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
MAIL_CREDENTIAL_ENCRYPTION_KEY = os.environ.get('MAIL_CREDENTIAL_ENCRYPTION_KEY', '')
# Optional JSON object mapping key_id -> Fernet key for dual-key rotation windows.
# The CredentialService parses/validates it; keep this as the raw env string so
# operators can mount it directly from a Kubernetes Secret.
MAIL_CREDENTIAL_ENCRYPTION_KEYS = os.environ.get('MAIL_CREDENTIAL_ENCRYPTION_KEYS', '')
MAIL_CREDENTIAL_KEY_ID = os.environ.get('MAIL_CREDENTIAL_KEY_ID', 'mail-credentials-v1')

# System Email Account Configuration
# Used for sending system emails (password resets, notifications, etc.)
# This account must be manually created via the management command: create_system_account
SYSTEM_EMAIL_ADDRESS = os.environ.get('SYSTEM_EMAIL_ADDRESS', 'noreply@shizuha.com')
SYSTEM_USER_ID = int(os.environ.get('SYSTEM_USER_ID', '1'))  # User ID that owns the system account

# OAuth provider configuration for adding external mail accounts (MAIL-41).
# client_id/client_secret injected via env / k8s Secret `shizuha-mail-oauth-providers`.
MAIL_OAUTH_PROVIDERS = {
    'gmail': {
        'client_id': os.environ.get('GMAIL_OAUTH_CLIENT_ID', ''),
        'client_secret': os.environ.get('GMAIL_OAUTH_CLIENT_SECRET', ''),
        'auth_url': 'https://accounts.google.com/o/oauth2/v2/auth',
        'token_url': 'https://oauth2.googleapis.com/token',
        'userinfo_url': 'https://openidconnect.googleapis.com/v1/userinfo',
        'scopes': [
            'https://mail.google.com/',
            'openid',
            'email',
        ],
        'imap_host': 'imap.gmail.com',
        'imap_port': 993,
        'imap_security': 'ssl',
        'smtp_host': 'smtp.gmail.com',
        'smtp_port': 587,
        'smtp_security': 'starttls',
    },
    'outlook': {
        'client_id': os.environ.get('OUTLOOK_OAUTH_CLIENT_ID', ''),
        'client_secret': os.environ.get('OUTLOOK_OAUTH_CLIENT_SECRET', ''),
        'auth_url': 'https://login.microsoftonline.com/common/oauth2/v2.0/authorize',
        'token_url': 'https://login.microsoftonline.com/common/oauth2/v2.0/token',
        'userinfo_url': 'https://graph.microsoft.com/v1.0/me',
        'scopes': [
            'https://outlook.office.com/IMAP.AccessAsUser.All',
            'https://outlook.office.com/SMTP.Send',
            'offline_access',
            'openid',
            'email',
        ],
        'imap_host': 'outlook.office365.com',
        'imap_port': 993,
        'imap_security': 'ssl',
        'smtp_host': 'smtp.office365.com',
        'smtp_port': 587,
        'smtp_security': 'starttls',
    },
}
# Public-facing redirect URI for the external provider OAuth callback.
# Must be registered in each provider's OAuth app console.
MAIL_OAUTH_REDIRECT_URI = os.environ.get(
    'MAIL_OAUTH_REDIRECT_URI',
    'https://shizuha.com/mail/api/accounts/oauth/callback/',
)

# Proxy settings for nginx
CSRF_COOKIE_SECURE = False
CSRF_COOKIE_HTTPONLY = False
CSRF_USE_SESSIONS = False

# Logging configuration
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '{levelname} {asctime} {module} {message}',
            'style': '{',
        },
        'simple': {
            'format': '{levelname} {message}',
            'style': '{',
        },
    },
    'filters': {
        'mail_redaction': {
            '()': 'mail.redaction.MailRedactionFilter',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
            'filters': ['mail_redaction'],
        },
    },
    'root': {
        'handlers': ['console'],
        'level': 'INFO',
    },
    'loggers': {
        'mail': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': False,
        },
        'django': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': False,
        },
    },
}


# ─────────── OAuth 2.0 Phase 2/4 — advisory claims + enforcement flag ───────
# Used by ShizuhaJWTAuthentication to log aud/iss mismatches and optionally
# reject tokens whose `aud` doesn't include this service. Defaults keep
# Phase 2 (advisory only) behavior; flip SHIZUHA_AUD_ENFORCE to True once
# advisory logs show clean traffic.
import os as _os
SHIZUHA_SERVICE_NAME = _os.environ.get('SHIZUHA_SERVICE_NAME', 'mail')
SHIZUHA_EXPECTED_ISSUER = _os.environ.get(
    'SHIZUHA_EXPECTED_ISSUER',
    'http://s1.tail.shizuha.com/id',
)
SHIZUHA_AUD_ENFORCE = _os.environ.get('SHIZUHA_AUD_ENFORCE', '').lower() in ('1', 'true', 'yes')
