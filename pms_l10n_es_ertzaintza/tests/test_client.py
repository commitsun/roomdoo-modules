# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html).
import os
from unittest import mock

import requests

from odoo.tests.common import TransactionCase

from ..models import ertzaintza_codes as codes
from ..models.ertzaintza_client import (
    ErtzaintzaClient,
    ErtzaintzaTransportError,
    parse_response,
)
from .certificate_utils import make_self_signed

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "responses")


def fixture(name):
    with open(os.path.join(FIXTURES, name), "rb") as handle:
        return handle.read()


class TestParseResponse(TransactionCase):
    def test_ok(self):
        response = parse_response(200, fixture("ok.xml"))
        self.assertTrue(response.ok)
        self.assertEqual(response.classification, "ok")
        self.assertEqual(response.request_uuid, "ef669758-39ba-451f-a600-a09298794895")
        self.assertEqual(response.state_code, "0")
        self.assertEqual(response.state_description, "OK")
        self.assertEqual(response.processing_date, "2026-09-15T12:45:05.003")
        self.assertEqual(response.errors, [])

    def test_duplicate_contract(self):
        response = parse_response(200, fixture("error_cto01.xml"))
        self.assertFalse(response.ok)
        self.assertEqual(response.error_codes, ["CTO01"])
        self.assertEqual(response.classification, "duplicate")
        self.assertEqual(response.request_uuid, "11111111-2222-3333-4444-555555555555")

    def test_data_errors_are_listed(self):
        response = parse_response(200, fixture("error_per43.xml"))
        self.assertEqual(response.error_codes, ["PER43", "PER05"])
        self.assertEqual(response.classification, "data")
        text = response.errors_text()
        self.assertIn("C_1|1_PER43_TI - Comunicación 1, Persona 1", text)
        self.assertIn("PER05", text)

    def test_empty_request_code(self):
        response = parse_response(200, fixture("error_pet07.xml"))
        self.assertEqual(response.error_codes, ["PET07"])
        self.assertEqual(response.classification, "data")

    def test_auth_error(self):
        response = parse_response(200, fixture("error_403.xml"))
        self.assertEqual(response.classification, "auth")
        self.assertEqual(response.request_uuid, "")

    def test_transient_error(self):
        response = parse_response(200, fixture("error_999.xml"))
        self.assertEqual(response.classification, "transient")

    def test_soap_fault(self):
        response = parse_response(500, fixture("soap_fault.xml"))
        self.assertFalse(response.ok)
        self.assertIn("Internal error", response.fault)
        self.assertEqual(response.classification, "transient")
        self.assertIn("SOAP Fault", response.errors_text())

    def test_non_xml_body(self):
        response = parse_response(502, b"<html>Bad gateway")
        self.assertFalse(response.ok)
        self.assertIn("HTTP 502", response.fault)
        self.assertEqual(response.classification, "transient")


class TestClient(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        key, certificate = make_self_signed("Client test")
        cls.client = ErtzaintzaClient(
            url="https://example.invalid/A19", certificate=certificate, private_key=key
        )

    def test_send_parses_mocked_answer(self):
        answer = mock.Mock(status_code=200, content=fixture("ok.xml"))
        with mock.patch.object(requests, "post", return_value=answer) as post:
            signed, response = self.client.send("PV", "A37777455", "<alt:peticion/>")
        self.assertTrue(response.ok)
        self.assertIn("<![CDATA[<alt:peticion/>]]>", signed)
        self.assertIn("BinarySecurityToken", signed)
        kwargs = post.call_args.kwargs
        self.assertEqual(kwargs["headers"]["SOAPAction"], codes.SOAP_ACTION)
        self.assertTrue(kwargs["verify"])

    def test_transport_error(self):
        with mock.patch.object(
            requests, "post", side_effect=requests.exceptions.ConnectTimeout("boom")
        ):
            with self.assertRaises(ErtzaintzaTransportError):
                self.client.send("PV", "A37777455", "<alt:peticion/>")
