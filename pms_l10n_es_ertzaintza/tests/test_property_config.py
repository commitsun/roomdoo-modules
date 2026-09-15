# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html).

import base64
import datetime
import tempfile

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from odoo.exceptions import ValidationError

from ..models.ertzaintza_codes import ENDPOINTS
from .common import TestErtzaintzaCommon


class TestErtzaintzaPropertyConfig(TestErtzaintzaCommon):
    def test_blocking_reasons_mention_certificate(self):
        # No AEAT certificate configured anywhere: the property is not
        # ready and the reason mentions the missing certificate.
        self.assertFalse(self.pms_property1.ertzaintza_ready)
        self.assertIn(
            "certificate",
            self.pms_property1.ertzaintza_blocking_reasons.lower(),
        )

    def test_blocking_reasons_empty_for_ses_property(self):
        self.assertTrue(self.pms_property_ses.ertzaintza_ready)
        self.assertEqual(self.pms_property_ses.ertzaintza_blocking_reasons, "")

    def test_lessor_code_too_long_raises(self):
        with self.assertRaises(ValidationError):
            self.pms_property1.write({"institution_lessor_id": "A" * 11})

    def test_establishment_code_too_long_raises(self):
        with self.assertRaises(ValidationError):
            self.pms_property1.write({"institution_property_id": "1" * 11})

    def test_ertzaintza_endpoint(self):
        self.pms_property1.ertzaintza_environment = "pre"
        self.assertEqual(self.pms_property1._ertzaintza_endpoint(), ENDPOINTS["pre"])
        self.pms_property1.ertzaintza_environment = "prod"
        self.assertEqual(self.pms_property1._ertzaintza_endpoint(), ENDPOINTS["prod"])

    def test_tls_verify_always_true_in_prod(self):
        self.pms_property1.ertzaintza_verify_tls = False
        self.pms_property1.ertzaintza_environment = "prod"
        self.assertTrue(self.pms_property1._ertzaintza_tls_verify())
        self.pms_property1.ertzaintza_environment = "pre"
        self.assertFalse(self.pms_property1._ertzaintza_tls_verify())

    def test_room_independent_ses_account_forbidden_on_ertzaintza_property(self):
        with self.assertRaises(ValidationError):
            self.env["pms.room"].create(
                {
                    "pms_property_id": self.pms_property1.id,
                    "name": "Room Ertzaintza 1",
                    "room_type_id": self.room_type1.id,
                    "capacity": 2,
                    "institution_independent_account": True,
                    "institution": "ses",
                }
            )

    def test_room_independent_ses_account_allowed_on_ses_property(self):
        room = self.env["pms.room"].create(
            {
                "pms_property_id": self.pms_property_ses.id,
                "name": "Room SES 1",
                "room_type_id": self.room_type1.id,
                "capacity": 2,
                "institution_independent_account": True,
                "institution": "ses",
            }
        )
        self.assertTrue(room)

    def test_certificate_resolution(self):
        common_name = "Ertzaintza Test Certificate"
        public_key_path, private_key_path = self._create_self_signed_certificate(
            common_name
        )
        certificate = self.env["l10n.es.aeat.certificate"].create(
            {
                "name": "Ertzaintza test certificate",
                "company_id": self.company1.id,
                "file": base64.b64encode(b"dummy"),
                "folder": "ertzaintza_test",
                "state": "active",
                "public_key": public_key_path,
                "private_key": private_key_path,
            }
        )
        self.pms_property1.ertzaintza_certificate_id = certificate
        public_key, private_key = self.pms_property1._ertzaintza_certificate_paths()
        self.assertEqual(public_key, public_key_path)
        self.assertEqual(private_key, private_key_path)
        self.assertIn(common_name, self.pms_property1.ertzaintza_certificate_subject)

    def _create_self_signed_certificate(self, common_name):
        """Generate a throwaway self-signed RSA certificate for tests.

        Returns (public_key_path, private_key_path) to two temporary PEM
        files that are removed once the test finishes.
        """
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = issuer = x509.Name(
            [x509.NameAttribute(NameOID.COMMON_NAME, common_name)]
        )
        cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(datetime.datetime.utcnow())
            .not_valid_after(datetime.datetime.utcnow() + datetime.timedelta(days=1))
            .sign(key, hashes.SHA256())
        )

        public_key_file = tempfile.NamedTemporaryFile(
            mode="wb", suffix=".pem", delete=False
        )
        public_key_file.write(cert.public_bytes(serialization.Encoding.PEM))
        public_key_file.close()
        self.addCleanup(self._remove_file, public_key_file.name)

        private_key_file = tempfile.NamedTemporaryFile(
            mode="wb", suffix=".pem", delete=False
        )
        private_key_file.write(
            key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.TraditionalOpenSSL,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )
        private_key_file.close()
        self.addCleanup(self._remove_file, private_key_file.name)

        return public_key_file.name, private_key_file.name

    @staticmethod
    def _remove_file(path):
        import os

        if os.path.exists(path):
            os.remove(path)
