# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html).
"""SOAP client for the Ertzaintza A19 ``envioFicheroXML`` operation.

No WSDL/zeep client is used on purpose: the WSDL shipped inside the SoapUI
project is outdated and the namespace of the answer differs between the
manual examples and the live service, so the envelope is built by hand with
lxml, signed with :mod:`.ertzaintza_wsse` and posted with ``requests``; the
answer is read by local element names.
"""
import logging
import re
from dataclasses import dataclass, field

import requests
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from lxml import etree
from lxml.etree import QName

from odoo import _
from odoo.exceptions import UserError

from . import ertzaintza_codes as codes
from .ertzaintza_wsse import sign_envelope

_logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 60
# Never let a connection string reach the chatter or the log.
SECRET_RE = re.compile(r"(password|pwd)=[^\s&]+", re.IGNORECASE)


class ErtzaintzaTransportError(Exception):
    """Network or HTTP failure: the request may or may not have arrived."""


@dataclass
class ErtzaintzaResponse:
    """What the A19 service answered, already read into plain values."""

    http_status: int = 0
    raw: str = ""
    errors: list = field(default_factory=list)  # [(raw_code, description), ...]
    request_uuid: str = ""
    state_code: str = ""
    state_description: str = ""
    processing_date: str = ""
    fault: str = ""

    @property
    def ok(self):
        return not self.errors and not self.fault and self.state_code == "0"

    @property
    def error_codes(self):
        return [codes.extract_error_code(code) for code, _description in self.errors]

    @property
    def classification(self):
        """``ok`` | ``duplicate`` | ``auth`` | ``transient`` | ``too_many`` | ``data``.

        An answer we could not read (a SOAP fault, an HTML error page, an
        empty body) is classified as transient: it is safer to retry than to
        mark a communication as rejected on something we did not understand.
        """
        if self.ok:
            return "ok"
        if self.errors:
            return codes.classify_error_codes(self.error_codes)
        return "transient"

    def errors_text(self):
        lines = [f"{code} - {description}" for code, description in self.errors]
        if self.fault:
            lines.append(f"SOAP Fault - {self.fault}")
        if self.state_code and self.state_code != "0":
            lines.append(
                f"codigoEstadoComunicacion {self.state_code} - {self.state_description}"
            )
        return "\n".join(lines)


def build_envelope(entity, lessor_code, solicitud_xml, application=None):
    """Return the unsigned ``soapenv:Envelope`` of ``envioFicheroXML``."""
    nsmap = {"soapenv": codes.SOAP_ENV_NS, "ws": codes.WS_NS}
    envelope = etree.Element(QName(codes.SOAP_ENV_NS, "Envelope"), nsmap=nsmap)
    etree.SubElement(envelope, QName(codes.SOAP_ENV_NS, "Header"))
    body = etree.SubElement(envelope, QName(codes.SOAP_ENV_NS, "Body"))
    operation = etree.SubElement(body, QName(codes.WS_NS, "envioFicheroXML"))
    header = etree.SubElement(operation, QName(codes.WS_NS, "cabecera"))
    etree.SubElement(header, "aplicacion").text = application or codes.APPLICATION_NAME
    etree.SubElement(header, "codigoArrendador").text = lessor_code or ""
    etree.SubElement(header, "tipoComunicacion").text = entity
    solicitud = etree.SubElement(operation, QName(codes.WS_NS, "solicitud"))
    solicitud.text = etree.CDATA(solicitud_xml or "")
    return envelope


def _find_all(root, name):
    """Elements by local name: the answer namespace is not stable."""
    return root.xpath(f"//*[local-name()='{name}']")


def parse_response(http_status, body):
    """Read an HTTP answer into an :class:`ErtzaintzaResponse`."""
    raw = body.decode("utf-8", errors="replace") if isinstance(body, bytes) else body
    response = ErtzaintzaResponse(http_status=http_status, raw=raw or "")
    try:
        root = etree.fromstring(response.raw.encode("utf-8"))
    except etree.XMLSyntaxError:
        response.fault = _(
            "HTTP %(status)s: the answer is not XML: %(body)s",
            status=http_status,
            body=response.raw[:500],
        )
        return response
    faults = _find_all(root, "faultstring")
    if faults:
        response.fault = (faults[0].text or "").strip()
    for error in _find_all(root, "error"):
        code = error.find("codigo")
        description = error.find("descripcion")
        if code is not None and code.text:
            response.errors.append(
                (
                    code.text.strip(),
                    (description.text or "").strip() if description is not None else "",
                )
            )
    answers = _find_all(root, "respuesta")
    if answers:
        request_uuid = answers[0].find("peticion")
        if request_uuid is not None and request_uuid.text:
            response.request_uuid = request_uuid.text.strip()
    results = _find_all(root, "resultado")
    if results:
        for tag, attribute in (
            ("codigoEstadoComunicacion", "state_code"),
            ("descEstadoComunicacion", "state_description"),
            ("fechaProcesamiento", "processing_date"),
        ):
            node = results[0].find(tag)
            if node is not None and node.text:
                setattr(response, attribute, node.text.strip())
    if http_status >= 400 and not response.errors and not response.fault:
        response.fault = _("HTTP %s", http_status)
    return response


class ErtzaintzaClient:
    """Signs and posts one request. Holds no state between calls."""

    def __init__(self, url, certificate, private_key, verify_tls=True, timeout=None):
        self.url = url
        self.certificate = certificate
        self.private_key = private_key
        self.verify_tls = verify_tls
        self.timeout = timeout or REQUEST_TIMEOUT

    @classmethod
    def from_property(cls, pms_property):
        """Build the client of a property from its configured certificate."""
        crt_path, key_path = pms_property._ertzaintza_certificate_paths()
        try:
            with open(crt_path, "rb") as handle:
                certificate = x509.load_pem_x509_certificate(handle.read())
            with open(key_path, "rb") as handle:
                private_key = serialization.load_pem_private_key(handle.read(), None)
        except (OSError, ValueError, TypeError) as error:
            raise UserError(
                _(
                    "The Ertzaintza certificate of %(property)s cannot be read: "
                    "%(error)s",
                    property=pms_property.display_name,
                    error=error,
                )
            ) from error
        return cls(
            url=pms_property._ertzaintza_endpoint(),
            certificate=certificate,
            private_key=private_key,
            verify_tls=pms_property._ertzaintza_tls_verify(),
        )

    def build_signed_request(self, entity, lessor_code, solicitud_xml):
        envelope = build_envelope(entity, lessor_code, solicitud_xml)
        sign_envelope(envelope, self.certificate, self.private_key)
        return etree.tostring(envelope, xml_declaration=True, encoding="UTF-8")

    def post(self, signed_request):
        """POST a signed envelope. Returns ``(status_code, body_bytes)``.

        Raises :class:`ErtzaintzaTransportError` when the request could not be
        completed; the caller decides whether to retry.
        """
        headers = {
            "Content-Type": "text/xml; charset=utf-8",
            "SOAPAction": codes.SOAP_ACTION,
        }
        try:
            answer = requests.post(
                self.url,
                data=signed_request,
                headers=headers,
                timeout=self.timeout,
                verify=self.verify_tls,
            )
        except requests.exceptions.RequestException as error:
            detail = SECRET_RE.sub(r"\1=***", str(error))
            raise ErtzaintzaTransportError(
                f"{type(error).__name__}: {detail}"
            ) from error
        return answer.status_code, answer.content

    def send(self, entity, lessor_code, solicitud_xml):
        """Sign, post and read. Returns ``(signed_request, response)``."""
        signed = self.build_signed_request(entity, lessor_code, solicitud_xml)
        status, body = self.post(signed)
        return signed.decode("utf-8"), parse_response(status, body)
