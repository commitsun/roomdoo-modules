# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html).
"""Self-signed certificates for the tests (the PRE certificate is never committed)."""
import datetime

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


def make_self_signed(common_name="Test Ertzaintza", days=365):
    """Return ``(private_key, certificate)`` cryptography objects."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name(
        [
            x509.NameAttribute(NameOID.COUNTRY_NAME, "ES"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Roomdoo tests"),
            x509.NameAttribute(NameOID.COMMON_NAME, common_name),
        ]
    )
    now = datetime.datetime.utcnow()
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=days))
        .sign(key, hashes.SHA256())
    )
    return key, certificate


def write_pem_files(key, certificate, directory):
    """Write ``cert.pem`` / ``key.pem`` into ``directory`` and return their paths."""
    crt_path = f"{directory}/cert.pem"
    key_path = f"{directory}/key.pem"
    with open(crt_path, "wb") as handle:
        handle.write(certificate.public_bytes(serialization.Encoding.PEM))
    with open(key_path, "wb") as handle:
        handle.write(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
    return crt_path, key_path
