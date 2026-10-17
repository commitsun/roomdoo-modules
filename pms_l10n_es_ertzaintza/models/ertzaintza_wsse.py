# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html).
"""WS-Security (WSSE) X.509 signature for the Ertzaintza A19 web service.

The A19 policy ``wss10_x509_token_with_message_protection`` requires:

* a ``wsu:Timestamp`` header,
* a ``ds:Signature`` (rsa-sha1 / sha1 / exclusive c14n) whose ``KeyInfo`` is a
  ``wsse:SecurityTokenReference`` pointing at a ``wsse:BinarySecurityToken``
  that carries the signing certificate (direct reference),
* three signed parts: the SOAP Body, the Timestamp and the token itself.

Modelled on ``l10n_es_facturae_face/models/wsse_signature.py`` (Creu Blanca),
with the header helpers written here instead of pulled from ``zeep``: this
module does not use a SOAP client, and the two helpers it needed are a dozen
lines. Validated against the pre-production endpoint on 2026-09-15: the
envelope produced by :func:`sign_envelope` is accepted with
``codigoEstadoComunicacion = 0``.
"""
import base64
import logging
import uuid
from datetime import datetime, timedelta

from lxml import etree
from lxml.etree import QName

_logger = logging.getLogger(__name__)

try:
    import xmlsig
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization
except (ImportError, OSError) as error:  # pragma: no cover
    _logger.info(error)

SOAP_ENV_NS = "http://schemas.xmlsoap.org/soap/envelope/"
WSSE_NS = (
    "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd"
)
WSU_NS = (
    "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd"
)
DS_NS = "http://www.w3.org/2000/09/xmldsig#"
X509_TOKEN_TYPE = (
    "http://docs.oasis-open.org/wss/2004/01/"
    "oasis-200401-wss-x509-token-profile-1.0#X509v3"
)
BASE64_ENCODING_TYPE = (
    "http://docs.oasis-open.org/wss/2004/01/"
    "oasis-200401-wss-soap-message-security-1.0#Base64Binary"
)
# The SoapUI project shipped by the Ertzaintza uses a 60 s time to live. The
# pre-production endpoint accepted 300 s, which leaves room for clock drift
# between our workers and their servers.
DEFAULT_TIMESTAMP_TTL = 300


def _security_header(envelope):
    """Return the ``wsse:Security`` header of ``envelope``, creating it."""
    header = envelope.find(QName(SOAP_ENV_NS, "Header"))
    if header is None:
        header = etree.Element(QName(SOAP_ENV_NS, "Header"))
        envelope.insert(0, header)
    security = header.find(QName(WSSE_NS, "Security"))
    if security is None:
        security = etree.SubElement(header, QName(WSSE_NS, "Security"))
    return security


def _ensure_id(node):
    """Give ``node`` a ``wsu:Id`` if it has none and return it."""
    attribute = QName(WSU_NS, "Id")
    node_id = node.get(attribute)
    if not node_id:
        node_id = "id-" + uuid.uuid4().hex
        node.set(attribute, node_id)
    return node_id


def _add_reference(signature, target):
    """Reference ``target`` from ``signature`` (wsu:Id, exc-c14n, sha1)."""
    reference = xmlsig.template.add_reference(
        signature, xmlsig.constants.TransformSha1, uri="#" + _ensure_id(target)
    )
    xmlsig.template.add_transform(reference, xmlsig.constants.TransformExclC14N)


def sign_envelope(envelope, certificate, private_key, ttl=DEFAULT_TIMESTAMP_TTL):
    """Sign a SOAP 1.1 envelope in place and return it.

    :param envelope: lxml root element (``soapenv:Envelope``) with a Body.
    :param certificate: ``cryptography`` X.509 certificate object.
    :param private_key: ``cryptography`` private key object.
    :param ttl: seconds until the ``wsu:Timestamp`` expires.
    """
    security = _security_header(envelope)
    security.set(QName(SOAP_ENV_NS, "mustUnderstand"), "1")

    token_id = "X509-" + uuid.uuid4().hex
    binary_token = etree.SubElement(
        security,
        QName(WSSE_NS, "BinarySecurityToken"),
        attrib={
            QName(WSU_NS, "Id"): token_id,
            "ValueType": X509_TOKEN_TYPE,
            "EncodingType": BASE64_ENCODING_TYPE,
        },
    )
    binary_token.text = base64.b64encode(
        certificate.public_bytes(encoding=serialization.Encoding.DER)
    ).decode()

    signature = xmlsig.template.create(
        c14n_method=xmlsig.constants.TransformExclC14N,
        sign_method=xmlsig.constants.TransformRsaSha1,
        ns="ds",
    )
    key_info = xmlsig.template.ensure_key_info(signature)
    token_reference = etree.SubElement(
        key_info, QName(WSSE_NS, "SecurityTokenReference")
    )
    etree.SubElement(
        token_reference,
        QName(WSSE_NS, "Reference"),
        attrib={"URI": "#" + token_id, "ValueType": X509_TOKEN_TYPE},
    )

    timestamp = etree.SubElement(security, QName(WSU_NS, "Timestamp"))
    created = datetime.utcnow().replace(microsecond=0)
    etree.SubElement(timestamp, QName(WSU_NS, "Created")).text = created.strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    etree.SubElement(timestamp, QName(WSU_NS, "Expires")).text = (
        created + timedelta(seconds=ttl)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")

    security.append(signature)

    context = xmlsig.SignatureContext()
    context.x509 = certificate
    context.public_key = certificate.public_key()
    context.private_key = private_key

    _add_reference(signature, envelope.find(QName(SOAP_ENV_NS, "Body")))
    _add_reference(signature, timestamp)
    _add_reference(signature, binary_token)
    context.sign(signature)
    return envelope


def verify_envelope(envelope):
    """Verify a signed envelope with the certificate it carries.

    Used by the tests; the Ertzaintza does not sign its answers. Raises when
    the signature does not match.
    """
    security = _security_header(envelope)
    signature = security.find(QName(DS_NS, "Signature"))
    token = security.find(QName(WSSE_NS, "BinarySecurityToken"))
    certificate = x509.load_der_x509_certificate(base64.b64decode(token.text))
    context = xmlsig.SignatureContext()
    context.public_key = certificate.public_key()
    context.verify(signature)
