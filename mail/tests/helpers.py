"""
Test helpers for Shizuha Mail tests.
"""
import time
import requests
from django.conf import settings


class MailpitClient:
    """
    Client for interacting with Mailpit API in tests.
    """

    def __init__(self, base_url=None):
        self.base_url = base_url or getattr(
            settings, 'MAILPIT_API_URL', 'http://mailpit:8025/api/v1'
        )

    def get_messages(self, limit=50):
        """Get all captured messages."""
        response = requests.get(f'{self.base_url}/messages', params={'limit': limit})
        response.raise_for_status()
        return response.json().get('messages', [])

    def get_message(self, message_id):
        """Get a single message with full body."""
        response = requests.get(f'{self.base_url}/message/{message_id}')
        response.raise_for_status()
        return response.json()

    def search(self, query):
        """Search messages."""
        response = requests.get(f'{self.base_url}/search', params={'query': query})
        response.raise_for_status()
        return response.json().get('messages', [])

    def clear(self):
        """Delete all messages."""
        response = requests.delete(f'{self.base_url}/messages')
        response.raise_for_status()

    def wait_for_message(self, subject, timeout=10, interval=0.5):
        """
        Wait for a message with the given subject to appear.

        Args:
            subject: Subject line to search for
            timeout: Maximum time to wait in seconds
            interval: Time between checks in seconds

        Returns:
            The matching message

        Raises:
            TimeoutError: If message not found within timeout
        """
        start = time.time()
        while time.time() - start < timeout:
            messages = self.search(f'subject:"{subject}"')
            if messages:
                return messages[0]
            time.sleep(interval)
        raise TimeoutError(f'Message with subject "{subject}" not found within {timeout}s')

    def wait_for_recipient(self, email, timeout=10, interval=0.5):
        """
        Wait for a message to the given recipient.

        Args:
            email: Recipient email address
            timeout: Maximum time to wait in seconds
            interval: Time between checks in seconds

        Returns:
            The matching message

        Raises:
            TimeoutError: If message not found within timeout
        """
        start = time.time()
        while time.time() - start < timeout:
            messages = self.search(f'to:"{email}"')
            if messages:
                return messages[0]
            time.sleep(interval)
        raise TimeoutError(f'Message to "{email}" not found within {timeout}s')

    def get_latest(self):
        """Get the most recent message."""
        messages = self.get_messages(limit=1)
        if messages:
            return self.get_message(messages[0]['ID'])
        return None


def create_test_jwt(user_id, email, username='testuser'):
    """
    Create a test JWT token for authentication.

    This creates a valid JWT that the FederatedUser authentication
    will accept. It includes all claims required by SimpleJWT.
    """
    import jwt
    from datetime import datetime, timedelta
    import uuid

    now = datetime.utcnow()
    payload = {
        'user_id': user_id,
        'username': username,
        'email': email,
        'exp': now + timedelta(hours=24),
        'iat': now,
        'jti': str(uuid.uuid4()),  # JWT ID required by SimpleJWT
        'token_type': 'access',    # Required by SimpleJWT
    }

    return jwt.encode(
        payload,
        settings.SECRET_KEY,
        algorithm='HS256'
    )
