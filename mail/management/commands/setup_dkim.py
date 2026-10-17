"""
Management command to generate DKIM keys for mail signing.
"""
import os
from pathlib import Path
from django.core.management.base import BaseCommand
from django.conf import settings


class Command(BaseCommand):
    help = 'Generate DKIM keys for mail signing'

    def add_arguments(self, parser):
        parser.add_argument(
            '--domain',
            default=getattr(settings, 'MAIL_DOMAIN', 'shizuha.com'),
            help='Domain for DKIM signing'
        )
        parser.add_argument(
            '--selector',
            default='mail',
            help='DKIM selector (usually "mail" or "default")'
        )
        parser.add_argument(
            '--output',
            default='/opt/stalwart-mail/etc/dkim',
            help='Output directory for DKIM keys'
        )
        parser.add_argument(
            '--key-size',
            type=int,
            default=2048,
            help='RSA key size (2048 or 4096 recommended)'
        )

    def handle(self, *args, **options):
        try:
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.primitives.asymmetric import rsa
            from cryptography.hazmat.backends import default_backend
        except ImportError:
            self.stdout.write(self.style.ERROR(
                'cryptography package not installed. Run: pip install cryptography'
            ))
            return

        domain = options['domain']
        selector = options['selector']
        output_dir = options['output']
        key_size = options['key_size']

        self.stdout.write(f'Generating {key_size}-bit RSA key pair for {domain}...')

        # Create output directory if needed
        Path(output_dir).mkdir(parents=True, exist_ok=True)

        # Generate RSA key pair
        private_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=key_size,
            backend=default_backend()
        )

        # Save private key (PEM format)
        private_pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption()
        )

        private_key_path = os.path.join(output_dir, f'{domain}.key')
        with open(private_key_path, 'wb') as f:
            f.write(private_pem)
        os.chmod(private_key_path, 0o600)

        self.stdout.write(self.style.SUCCESS(f'Private key saved to: {private_key_path}'))

        # Get public key for DNS record
        public_key = private_key.public_key()
        public_pem = public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo
        )

        # Format for DNS TXT record
        public_b64 = public_pem.decode()
        public_b64 = public_b64.replace('-----BEGIN PUBLIC KEY-----', '')
        public_b64 = public_b64.replace('-----END PUBLIC KEY-----', '')
        public_b64 = public_b64.replace('\n', '')

        dns_record = f'v=DKIM1; k=rsa; p={public_b64}'

        # Save DNS record to file
        dns_record_path = os.path.join(output_dir, f'{domain}.dns.txt')
        with open(dns_record_path, 'w') as f:
            f.write(f'{selector}._domainkey.{domain} TXT "{dns_record}"\n')

        self.stdout.write(self.style.SUCCESS(f'DNS record saved to: {dns_record_path}'))

        # Print DNS record for easy copy
        self.stdout.write('')
        self.stdout.write(self.style.WARNING('Add this DNS TXT record to your domain:'))
        self.stdout.write('')
        self.stdout.write(f'  Name: {selector}._domainkey.{domain}')
        self.stdout.write(f'  Type: TXT')
        self.stdout.write(f'  Value: {dns_record[:100]}...')
        self.stdout.write('')
        self.stdout.write(f'Full record saved to: {dns_record_path}')
        self.stdout.write('')
        self.stdout.write(self.style.WARNING('Remember to also add SPF and DMARC records:'))
        self.stdout.write('')
        self.stdout.write(f'  SPF:   {domain} TXT "v=spf1 mx a:mail.{domain} ~all"')
        self.stdout.write(f'  DMARC: _dmarc.{domain} TXT "v=DMARC1; p=quarantine; rua=mailto:dmarc@{domain}"')
