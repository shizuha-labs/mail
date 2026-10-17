"""
WSGI config for mailmanager project.
"""
import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'mailmanager.settings')

application = get_wsgi_application()
