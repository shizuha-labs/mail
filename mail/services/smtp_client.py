"""
SMTP Client for sending emails through any SMTP server.

Supports SSL/TLS, STARTTLS, and plain connections.
"""
import logging
import base64
import smtplib
import ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email.mime.application import MIMEApplication
from email import encoders
from email.utils import formataddr, formatdate, make_msgid
from typing import Optional, Union

from django.conf import settings

logger = logging.getLogger(__name__)


class SMTPError(Exception):
    """Base exception for SMTP errors."""
    pass


class SMTPAuthError(SMTPError):
    """Authentication failed."""
    pass


class SMTPConnectionError(SMTPError):
    """Connection failed."""
    pass


class SMTPSSLError(SMTPError):
    """SSL/TLS error."""
    pass


class SMTPSendError(SMTPError):
    """Failed to send email."""
    pass


class SMTPClient:
    """
    SMTP client for sending emails through any SMTP server.

    Supports:
    - SSL/TLS (typically port 465)
    - STARTTLS (typically port 587)
    - No encryption (typically port 25)
    """

    def __init__(
        self,
        host: str,
        port: int = 587,
        username: str = None,
        password: str = None,
        security: str = 'starttls',  # 'ssl', 'starttls', 'none'
        timeout: int = 30,
        from_address: str = None,
        from_name: str = None,
        oauth_access_token: str = None,
    ):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.security = security
        self.timeout = timeout
        self.from_address = from_address
        self.from_name = from_name
        self.oauth_access_token = oauth_access_token or ''
        self._connection: Optional[smtplib.SMTP] = None
        self._connected = False

    def connect(self) -> bool:
        """
        Establish connection to the SMTP server.

        Returns:
            True if connection successful

        Raises:
            SMTPConnectionError: If connection fails
            SMTPSSLError: If SSL/TLS setup fails
            SMTPAuthError: If authentication fails
        """
        verify_ssl = getattr(settings, 'STALWART_VERIFY_SSL', True)

        try:
            if self.security == 'ssl':
                # Direct SSL connection (typically port 465)
                context = ssl.create_default_context()
                if not verify_ssl:
                    context.check_hostname = False
                    context.verify_mode = ssl.CERT_NONE
                self._connection = smtplib.SMTP_SSL(
                    self.host,
                    self.port,
                    context=context,
                    timeout=self.timeout
                )
            else:
                # Plain connection (typically port 587 or 25)
                self._connection = smtplib.SMTP(
                    self.host,
                    self.port,
                    timeout=self.timeout
                )

                # Say hello
                self._connection.ehlo()

                if self.security == 'starttls':
                    # Upgrade to TLS
                    context = ssl.create_default_context()
                    if not verify_ssl:
                        context.check_hostname = False
                        context.verify_mode = ssl.CERT_NONE
                    self._connection.starttls(context=context)
                    self._connection.ehlo()

        except ssl.SSLError as e:
            raise SMTPSSLError(f"SSL/TLS error connecting to {self.host}:{self.port}: {e}")
        except smtplib.SMTPException as e:
            raise SMTPConnectionError(f"SMTP error connecting to {self.host}:{self.port}: {e}")
        except (OSError, TimeoutError) as e:
            raise SMTPConnectionError(f"Failed to connect to {self.host}:{self.port}: {e}")

        # Authenticate
        if self.username and self.oauth_access_token:
            try:
                payload = (
                    f'user={self.username}\x01auth=Bearer '
                    f'{self.oauth_access_token}\x01\x01'
                ).encode()
                code, response = self._connection.docmd(
                    'AUTH', 'XOAUTH2 ' + base64.b64encode(payload).decode(),
                )
                if code != 235:
                    raise smtplib.SMTPAuthenticationError(code, response)
            except smtplib.SMTPAuthenticationError as e:
                self._connection.quit()
                raise SMTPAuthError(f'OAuth authentication failed: {e}')
            except smtplib.SMTPException as e:
                self._connection.quit()
                raise SMTPAuthError(f'OAuth login error: {e}')
        elif self.username and self.password:
            try:
                self._connection.login(self.username, self.password)
            except smtplib.SMTPAuthenticationError as e:
                self._connection.quit()
                raise SMTPAuthError(f"Authentication failed: {e}")
            except smtplib.SMTPException as e:
                self._connection.quit()
                raise SMTPAuthError(f"Login error: {e}")

        self._connected = True
        return True

    def disconnect(self):
        """Close the SMTP connection."""
        if self._connection:
            try:
                self._connection.quit()
            except Exception:
                pass
            self._connection = None
            self._connected = False

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.disconnect()
        return False

    def _ensure_connected(self):
        """Ensure we have an active connection."""
        if not self._connected or not self._connection:
            raise SMTPError("Not connected to SMTP server")

    def test_connection(self) -> tuple[bool, str]:
        """
        Test the SMTP connection with current credentials.

        Returns:
            Tuple of (success: bool, message: str)
        """
        try:
            self.connect()
            # Try NOOP to verify connection is working
            self._connection.noop()
            self.disconnect()
            return True, "Connection successful"
        except SMTPAuthError as e:
            return False, f"Authentication failed: {e}"
        except SMTPSSLError as e:
            return False, f"SSL/TLS error: {e}"
        except SMTPConnectionError as e:
            return False, f"Connection failed: {e}"
        except Exception as e:
            return False, f"Error: {e}"
        finally:
            self.disconnect()

    def send(
        self,
        to_addresses: list[str],
        subject: str,
        body_text: str = None,
        body_html: str = None,
        cc_addresses: list[str] = None,
        bcc_addresses: list[str] = None,
        reply_to: str = None,
        attachments: list[dict] = None,
        from_address: str = None,
        from_name: str = None,
        in_reply_to: str = None,
        references: list[str] = None,
        custom_headers: dict = None,
    ) -> dict:
        """
        Send an email.

        Args:
            to_addresses: List of recipient email addresses
            subject: Email subject
            body_text: Plain text body (optional if body_html provided)
            body_html: HTML body (optional if body_text provided)
            cc_addresses: List of CC recipients
            bcc_addresses: List of BCC recipients
            reply_to: Reply-To address
            attachments: List of attachment dicts with keys:
                - filename: Attachment filename
                - content: Bytes content
                - content_type: MIME type (optional)
            from_address: Override sender address
            from_name: Override sender display name
            in_reply_to: Message-ID being replied to
            references: List of Message-IDs in thread
            custom_headers: Dict of additional headers

        Returns:
            Dict with send result info

        Raises:
            SMTPSendError: If sending fails
        """
        self._ensure_connected()

        sender_address = from_address or self.from_address
        sender_name = from_name or self.from_name

        if not sender_address:
            raise SMTPError("No from address configured")

        if not to_addresses:
            raise SMTPError("No recipients specified")

        if not body_text and not body_html:
            raise SMTPError("No message body provided")

        # Build the message
        if body_html and body_text:
            # Multipart alternative (both text and HTML)
            msg = MIMEMultipart('alternative')
            msg.attach(MIMEText(body_text, 'plain', 'utf-8'))
            msg.attach(MIMEText(body_html, 'html', 'utf-8'))
        elif body_html:
            msg = MIMEMultipart('alternative')
            msg.attach(MIMEText(body_html, 'html', 'utf-8'))
        else:
            msg = MIMEText(body_text, 'plain', 'utf-8')

        # If we have attachments, wrap in multipart/mixed
        if attachments:
            mixed_msg = MIMEMultipart('mixed')
            mixed_msg.attach(msg)

            for attachment in attachments:
                self._attach_file(mixed_msg, attachment)

            msg = mixed_msg

        # Set headers
        if sender_name:
            msg['From'] = formataddr((sender_name, sender_address))
        else:
            msg['From'] = sender_address

        msg['To'] = ', '.join(to_addresses)
        msg['Subject'] = subject
        msg['Date'] = formatdate(localtime=True)
        msg['Message-ID'] = make_msgid()

        if cc_addresses:
            msg['Cc'] = ', '.join(cc_addresses)

        if reply_to:
            msg['Reply-To'] = reply_to

        if in_reply_to:
            msg['In-Reply-To'] = in_reply_to

        if references:
            msg['References'] = ' '.join(references)

        if custom_headers:
            for header, value in custom_headers.items():
                msg[header] = value

        # Build full recipient list
        all_recipients = list(to_addresses)
        if cc_addresses:
            all_recipients.extend(cc_addresses)
        if bcc_addresses:
            all_recipients.extend(bcc_addresses)

        # Send the email
        try:
            result = self._connection.sendmail(
                sender_address,
                all_recipients,
                msg.as_string()
            )

            return {
                'success': True,
                'message_id': msg['Message-ID'],
                'rejected': result,  # Dict of rejected recipients
            }

        except smtplib.SMTPRecipientsRefused as e:
            raise SMTPSendError(f"All recipients refused: {e}")
        except smtplib.SMTPSenderRefused as e:
            raise SMTPSendError(f"Sender refused: {e}")
        except smtplib.SMTPDataError as e:
            raise SMTPSendError(f"Data error: {e}")
        except smtplib.SMTPException as e:
            raise SMTPSendError(f"SMTP error: {e}")

    def _attach_file(self, msg: MIMEMultipart, attachment: dict):
        """Add an attachment to the message."""
        filename = attachment.get('filename', 'attachment')
        content = attachment.get('content', b'')
        content_type = attachment.get('content_type', 'application/octet-stream')

        if '/' in content_type:
            maintype, subtype = content_type.split('/', 1)
        else:
            maintype = 'application'
            subtype = 'octet-stream'

        if maintype == 'text':
            # Text attachment
            if isinstance(content, bytes):
                content = content.decode('utf-8', errors='replace')
            part = MIMEText(content, _subtype=subtype)
        elif maintype == 'application':
            # Application attachment
            part = MIMEApplication(content, _subtype=subtype)
        else:
            # Generic attachment
            part = MIMEBase(maintype, subtype)
            part.set_payload(content)
            encoders.encode_base64(part)

        part.add_header(
            'Content-Disposition',
            'attachment',
            filename=filename
        )
        msg.attach(part)

    def send_simple(
        self,
        to: Union[str, list[str]],
        subject: str,
        body: str,
        html: bool = False,
    ) -> dict:
        """
        Simple email sending interface.

        Args:
            to: Recipient email address(es)
            subject: Email subject
            body: Email body
            html: If True, treat body as HTML

        Returns:
            Dict with send result
        """
        if isinstance(to, str):
            to = [to]

        if html:
            return self.send(to_addresses=to, subject=subject, body_html=body)
        else:
            return self.send(to_addresses=to, subject=subject, body_text=body)


def create_smtp_client_from_account(
    account, credential_service, oauth_access_token: str = '',
) -> SMTPClient:
    """
    Create an SMTP client from a MailAccount instance.

    Args:
        account: MailAccount model instance
        credential_service: CredentialService instance for decrypting passwords

    Returns:
        Configured SMTPClient instance
    """
    from .credential_service import DecryptionError

    # Request-scoped managed-account tokens take precedence. Other OAuth
    # providers retain their encrypted-at-rest access-token path.
    if not oauth_access_token and account.auth_type == 'oauth2' and account.encrypted_oauth_access_token:
        oauth_access_token = credential_service.decrypt_password(
            account.encrypted_oauth_access_token
        )

    # Get password
    password = ''
    if account.encrypted_outgoing_password:
        try:
            password = credential_service.decrypt_password(account.encrypted_outgoing_password)
        except DecryptionError:
            logger.warning("Failed to decrypt outgoing password for account %s; denying send", account.id)
            raise
    elif account.encrypted_incoming_password:
        # Fall back to incoming password if outgoing not set
        try:
            password = credential_service.decrypt_password(account.encrypted_incoming_password)
        except DecryptionError:
            logger.warning("Failed to decrypt incoming password for account %s; denying send", account.id)
            raise

    username = account.outgoing_username or account.incoming_username

    return SMTPClient(
        host=account.outgoing_host,
        port=account.outgoing_port,
        username=username,
        password=password,
        security=account.outgoing_security,
        from_address=account.email_address,
        from_name=account.from_name or account.display_name,
        oauth_access_token=oauth_access_token,
    )
