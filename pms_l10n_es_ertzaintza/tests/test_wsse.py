# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html).
from datetime import datetime

from lxml import etree

from odoo.tests.common import TransactionCase

from ..models import ertzaintza_codes as codes
from ..models.ertzaintza_client import build_envelope
from ..models.ertzaintza_wsse import sign_envelope, verify_envelope
from .certificate_utils import make_self_signed

NS = {
    "soapenv": codes.SOAP_ENV_NS,
    "wsse": "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd",
    "wsu": "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd",
    "ds": "http://www.w3.org/2000/09/xmldsig#",
    "ws": codes.WS_NS,
}


class TestWsseSignature(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.key, cls.certificate = make_self_signed("WSSE test")

    def _signed(self, ttl=300):
        envelope = build_envelope("PV", "A37777455", "<alt:peticion/>")
        return sign_envelope(envelope, self.certificate, self.key, ttl=ttl)

    def test_envelope_structure(self):
        envelope = build_envelope("RH", "A37777455", "<x>&</x>")
        header = envelope.xpath("//ws:cabecera", namespaces=NS)[0]
        self.assertEqual(header.find("aplicacion").text, codes.APPLICATION_NAME)
        self.assertEqual(header.find("codigoArrendador").text, "A37777455")
        self.assertEqual(header.find("tipoComunicacion").text, "RH")
        raw = etree.tostring(envelope).decode()
        self.assertIn("<![CDATA[<x>&</x>]]>", raw, "solicitud must travel as CDATA")

    def test_signature_has_three_references(self):
        envelope = self._signed()
        security = envelope.xpath("//soapenv:Header/wsse:Security", namespaces=NS)
        self.assertEqual(len(security), 1)
        self.assertEqual(security[0].get("{%s}mustUnderstand" % codes.SOAP_ENV_NS), "1")
        references = envelope.xpath(
            "//ds:Signature/ds:SignedInfo/ds:Reference", namespaces=NS
        )
        self.assertEqual(len(references), 3)
        uris = {ref.get("URI") for ref in references}
        body_id = envelope.xpath("//soapenv:Body/@wsu:Id", namespaces=NS)[0]
        timestamp_id = envelope.xpath("//wsu:Timestamp/@wsu:Id", namespaces=NS)[0]
        token_id = envelope.xpath("//wsse:BinarySecurityToken/@wsu:Id", namespaces=NS)[
            0
        ]
        self.assertEqual(uris, {"#" + body_id, "#" + timestamp_id, "#" + token_id})
        # KeyInfo points to the BinarySecurityToken (direct reference)
        key_ref = envelope.xpath(
            "//ds:KeyInfo/wsse:SecurityTokenReference/wsse:Reference/@URI",
            namespaces=NS,
        )[0]
        self.assertEqual(key_ref, "#" + token_id)

    def test_algorithms(self):
        envelope = self._signed()
        self.assertEqual(
            envelope.xpath("//ds:CanonicalizationMethod/@Algorithm", namespaces=NS)[0],
            "http://www.w3.org/2001/10/xml-exc-c14n#",
        )
        self.assertEqual(
            envelope.xpath("//ds:SignatureMethod/@Algorithm", namespaces=NS)[0],
            "http://www.w3.org/2000/09/xmldsig#rsa-sha1",
        )
        digests = set(envelope.xpath("//ds:DigestMethod/@Algorithm", namespaces=NS))
        self.assertEqual(digests, {"http://www.w3.org/2000/09/xmldsig#sha1"})

    def test_timestamp_utc_and_ttl(self):
        envelope = self._signed(ttl=60)
        created = envelope.xpath("//wsu:Timestamp/wsu:Created/text()", namespaces=NS)[0]
        expires = envelope.xpath("//wsu:Timestamp/wsu:Expires/text()", namespaces=NS)[0]
        fmt = "%Y-%m-%dT%H:%M:%SZ"
        delta = datetime.strptime(expires, fmt) - datetime.strptime(created, fmt)
        self.assertEqual(delta.total_seconds(), 60)
        self.assertLess(
            abs((datetime.utcnow() - datetime.strptime(created, fmt)).total_seconds()),
            30,
        )

    def test_signature_verifies_after_serialization(self):
        envelope = self._signed()
        serialized = etree.tostring(envelope, xml_declaration=True, encoding="UTF-8")
        reparsed = etree.fromstring(serialized)
        verify_envelope(reparsed)  # raises on failure

    def test_tampered_body_fails_verification(self):
        envelope = self._signed()
        header = envelope.xpath("//ws:cabecera/codigoArrendador", namespaces=NS)[0]
        header.text = "B00000000"
        reparsed = etree.fromstring(etree.tostring(envelope))
        # xmlsig raises a library specific error; any exception means the
        # tampered envelope was rejected, which is what matters here.
        with self.assertRaises(Exception):  # noqa: B017
            verify_envelope(reparsed)
