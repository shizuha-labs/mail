"""
Credential encryption service for storing email passwords securely.

MAIL-9 stores credentials as versioned encrypted envelopes. The ciphertext is
kept in the Mail database; the Fernet key is supplied separately via
MAIL_CREDENTIAL_ENCRYPTION_KEY (Kubernetes Secret / external secret), never from
DB state.
"""
import base64
import binascii
import json
import logging
from typing import Optional
from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings

logger = logging.getLogger(__name__)


class CredentialServiceError(Exception):
    """Base exception for credential service errors."""
    pass


class EncryptionKeyNotConfigured(CredentialServiceError):
    """Raised when the encryption key is not configured."""
    pass


class DecryptionError(CredentialServiceError):
    """Raised when decryption fails."""
    pass


class CredentialService:
    """
    Service for encrypting and decrypting email credentials.

    Uses Fernet encryption which provides:
    - AES-128-CBC encryption
    - HMAC-SHA256 for authentication
    - Timestamp-based token versioning

    Stored DB value is a JSON envelope, encoded as bytes:
    {
      "version": 1,
      "alg": "fernet",
      "key_id": "mail-credentials-v1",
      "ciphertext": "<base64 Fernet token>"
    }

    Legacy raw Fernet tokens are still readable so older rows can be migrated
    one-way to envelopes by `manage.py migrate_mail_credentials`.
    """

    _instance = None
    ENVELOPE_VERSION = 1
    ENVELOPE_ALG = 'fernet'

    def __init__(self):
        key = getattr(settings, 'MAIL_CREDENTIAL_ENCRYPTION_KEY', None)
        self.key_id = getattr(settings, 'MAIL_CREDENTIAL_KEY_ID', 'mail-credentials-v1')
        self._ciphers = {}

        configured_keys = self._load_configured_keys(key)
        if not configured_keys:
            raise EncryptionKeyNotConfigured(
                "MAIL_CREDENTIAL_ENCRYPTION_KEY not configured. "
                "Generate a key with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
            )

        for configured_key_id, configured_key in configured_keys.items():
            try:
                # Key should be a base64-encoded 32-byte key
                if isinstance(configured_key, str):
                    configured_key = configured_key.encode()
                self._ciphers[configured_key_id] = Fernet(configured_key)
            except Exception as e:
                raise CredentialServiceError(f"Invalid encryption key format for {configured_key_id}: {e}")

        if self.key_id not in self._ciphers:
            raise CredentialServiceError(
                f"MAIL_CREDENTIAL_KEY_ID '{self.key_id}' has no configured encryption key."
            )
        self._cipher = self._ciphers[self.key_id]

    def _load_configured_keys(self, primary_key) -> dict[str, str]:
        """Load current + historical Fernet keys for rotation.

        MAIL_CREDENTIAL_ENCRYPTION_KEYS may be a JSON object mapping key_id to
        Fernet key. MAIL_CREDENTIAL_ENCRYPTION_KEY remains the current-key
        convenience path and is added under MAIL_CREDENTIAL_KEY_ID.
        """
        keys = {}
        mapping = getattr(settings, 'MAIL_CREDENTIAL_ENCRYPTION_KEYS', None)
        if mapping:
            if isinstance(mapping, str):
                try:
                    mapping = json.loads(mapping)
                except json.JSONDecodeError as exc:
                    raise CredentialServiceError("MAIL_CREDENTIAL_ENCRYPTION_KEYS must be a JSON object.") from exc
            if not isinstance(mapping, dict):
                raise CredentialServiceError("MAIL_CREDENTIAL_ENCRYPTION_KEYS must be a key_id -> key mapping.")
            keys.update({str(k): v for k, v in mapping.items() if v})
        if primary_key:
            keys[self.key_id] = primary_key
        return keys

    @classmethod
    def get_instance(cls) -> 'CredentialService':
        """Get singleton instance of CredentialService."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def _normalize_stored(self, value) -> bytes:
        if value is None:
            return b''
        if isinstance(value, memoryview):
            return bytes(value)
        if isinstance(value, bytes):
            return value
        if isinstance(value, str):
            return value.encode('utf-8')
        return bytes(value)

    def _envelope(self, fernet_token: bytes) -> bytes:
        payload = {
            'version': self.ENVELOPE_VERSION,
            'alg': self.ENVELOPE_ALG,
            'key_id': self.key_id,
            'ciphertext': base64.urlsafe_b64encode(fernet_token).decode('ascii'),
        }
        return json.dumps(payload, sort_keys=True, separators=(',', ':')).encode('utf-8')

    def _looks_like_envelope_json_bytes(self, encrypted: bytes) -> bool:
        """Best-effort shape check for malformed JSON envelope bytes.

        Invalid JSON such as a literal plaintext password `{secret}` should still
        migrate as plaintext. Bytes that contain MAIL-9 envelope field names are
        treated as a corrupted/truncated envelope and fail closed instead of
        being re-encrypted as plaintext garbage.
        """
        lowered = encrypted.lower()
        return any(marker in lowered for marker in (b'"alg"', b'"key_id"', b'"ciphertext"'))

    def _parse_envelope(self, encrypted: bytes, strict: bool = True) -> Optional[dict]:
        encrypted = self._normalize_stored(encrypted)
        if not encrypted or not encrypted.lstrip().startswith(b'{'):
            return None
        try:
            envelope = json.loads(encrypted.decode('utf-8'))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            if not strict and not self._looks_like_envelope_json_bytes(encrypted):
                return None
            raise DecryptionError("Credential envelope is malformed or corrupted.") from exc
        if not isinstance(envelope, dict):
            if not strict:
                return None
            raise DecryptionError("Credential envelope is malformed or corrupted.")

        keys = set(envelope.keys())
        envelope_like = bool({'alg', 'key_id', 'ciphertext'} & keys)
        if not strict and not envelope_like:
            return None

        if envelope.get('version') != self.ENVELOPE_VERSION or envelope.get('alg') != self.ENVELOPE_ALG:
            raise DecryptionError("Unsupported credential envelope version or algorithm.")
        if not envelope.get('key_id'):
            raise DecryptionError("Credential envelope is missing key_id.")
        if not envelope.get('ciphertext'):
            raise DecryptionError("Credential envelope is missing ciphertext.")
        return envelope

    def _cipher_for_key_id(self, key_id: str):
        try:
            return self._ciphers[key_id]
        except KeyError as exc:
            raise DecryptionError("Credential envelope key_id is not configured for decryption.") from exc

    def _looks_like_fernet_token(self, value: bytes) -> bool:
        """Return True for raw Fernet token shape without decrypting it."""
        value = self._normalize_stored(value).strip()
        if not value or value.startswith(b'{'):
            return False
        padded = value + b'=' * ((4 - len(value) % 4) % 4)
        try:
            decoded = base64.urlsafe_b64decode(padded)
        except (binascii.Error, ValueError):
            return False
        # Fernet tokens are version byte 0x80 + timestamp + IV + ciphertext + HMAC.
        return len(decoded) >= 57 and decoded[0] == 0x80

    def _decrypt_raw_fernet_token(self, token: bytes) -> str:
        for cipher in self._ciphers.values():
            try:
                return cipher.decrypt(token).decode('utf-8')
            except InvalidToken:
                continue
        raise InvalidToken

    def is_envelope(self, encrypted: bytes) -> bool:
        """Return True when the stored value is already a MAIL-9 envelope."""
        try:
            return self._parse_envelope(encrypted) is not None
        except DecryptionError:
            return False

    def encrypt_password(self, password: str) -> bytes:
        """
        Encrypt a password for storage.

        Args:
            password: The plaintext password to encrypt

        Returns:
            Versioned encrypted envelope as bytes (suitable for BinaryField)
        """
        if not password:
            return b''

        token = self._cipher.encrypt(password.encode('utf-8'))
        return self._envelope(token)

    def decrypt_password(self, encrypted: bytes) -> str:
        """
        Decrypt a stored password.

        Args:
            encrypted: The encrypted password bytes/envelope

        Returns:
            The decrypted plaintext password

        Raises:
            DecryptionError: If decryption fails (invalid key or corrupted data)
        """
        encrypted = self._normalize_stored(encrypted)
        if not encrypted:
            return ''

        try:
            envelope = self._parse_envelope(encrypted)
            if envelope is not None:
                cipher = self._cipher_for_key_id(envelope.get('key_id', self.key_id))
                encrypted = base64.urlsafe_b64decode(envelope['ciphertext'].encode('ascii'))
                return cipher.decrypt(encrypted).decode('utf-8')
            return self._decrypt_raw_fernet_token(encrypted)
        except InvalidToken:
            raise DecryptionError(
                "Failed to decrypt credential. This may indicate the encryption key has changed "
                "or the data is corrupted."
            )
        except DecryptionError:
            raise
        except Exception as e:
            raise DecryptionError(f"Credential decryption failed: {e}")

    def can_decrypt(self, encrypted: bytes) -> bool:
        """
        Test if encrypted data can be decrypted with the current key.

        Args:
            encrypted: The encrypted password bytes/envelope

        Returns:
            True if decryption succeeds, False otherwise
        """
        if not encrypted:
            return True

        try:
            self.decrypt_password(encrypted)
            return True
        except Exception:
            return False

    def migrate_stored_value(self, stored: bytes) -> bytes:
        """
        Return a MAIL-9 encrypted envelope for an existing stored value.

        Existing envelopes are returned unchanged. Legacy raw Fernet tokens are
        decrypted and re-enveloped. Values that are not decryptable as Fernet are
        treated as plaintext only if they are valid UTF-8; otherwise the caller
        gets a redacted migration error and must investigate manually.
        """
        stored = self._normalize_stored(stored)
        if not stored:
            return b''
        envelope = self._parse_envelope(stored, strict=False)
        if envelope is not None:
            if envelope.get('key_id') == self.key_id:
                return stored
            plaintext = self.decrypt_password(stored)
            return self.encrypt_password(plaintext)
        try:
            plaintext = self._decrypt_raw_fernet_token(stored)
        except InvalidToken:
            if self._looks_like_fernet_token(stored):
                raise DecryptionError(
                    "Stored credential looks like a Fernet token but cannot be decrypted with configured keys; "
                    "aborting migration to avoid re-encrypting corrupted/wrong-key data."
                )
            try:
                plaintext = stored.decode('utf-8')
            except UnicodeDecodeError as exc:
                raise DecryptionError("Stored credential is neither a valid legacy token nor UTF-8 plaintext.") from exc
        return self.encrypt_password(plaintext)


def get_credential_service() -> CredentialService:
    """
    Get the credential service instance.

    This is the recommended way to access the credential service.

    Returns:
        CredentialService singleton instance

    Raises:
        EncryptionKeyNotConfigured: If the encryption key is not set
    """
    return CredentialService.get_instance()


def generate_encryption_key() -> str:
    """
    Generate a new Fernet encryption key.

    Returns:
        A base64-encoded 32-byte Fernet key as a string
    """
    return Fernet.generate_key().decode()
