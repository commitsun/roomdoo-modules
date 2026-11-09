# Copyright 2026 Commit [Sun]
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
"""What the REST service answers: status, body and headers."""

from unittest.mock import MagicMock, patch

from odoo.tests import tagged

from odoo.addons.base_rest.controllers.main import _PseudoCollection
from odoo.addons.component.core import WorkContext

from .common import PASSWORD, VISA, PaymentCardCase

REQUEST = "odoo.addons.pms_payment_card.services.pms_folio_service.request"


@tagged("post_install", "-at_install")
class TestPaymentCardService(PaymentCardCase):
    def _call(self, method, *args, password=None, body=None):
        collection = _PseudoCollection("pms.services", self.env(user=self.receptionist))
        work = WorkContext(
            model_name="rest.service.registration", collection=collection
        )
        service = work.component(usage="folios")
        fake_request = MagicMock()
        fake_request.httprequest.headers = (
            {"X-Card-Password": password} if password is not None else {}
        )
        fake_request.httprequest.remote_addr = "192.0.2.10"
        if isinstance(body, Exception):
            fake_request.get_json_data.side_effect = body
        else:
            fake_request.get_json_data.return_value = body
        fake_request.make_json_response.side_effect = lambda data, headers, status: (
            data,
            dict(headers),
            status,
        )
        with patch(REQUEST, fake_request):
            return getattr(service, method)(self.folio.id, *args)

    def test_summary(self):
        body, headers, status = self._call("get_folio_payment_card")

        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "none")
        self.assertEqual(headers["Cache-Control"], "no-store")

    def test_add_answers_201(self):
        body, _headers, status = self._call("create_folio_payment_card", body=VISA)

        self.assertEqual(status, 201)
        self.assertEqual(body["source"]["type"], "manual")

    def test_errors_carry_a_code_and_a_description(self):
        self._call("create_folio_payment_card", body=VISA)

        body, headers, status = self._call(
            "get_folio_payment_card_details", password="wrong password"
        )

        self.assertEqual(status, 422)
        self.assertEqual(body["code"], "invalid_card_password")
        self.assertTrue(body["description"])
        self.assertEqual(headers["Cache-Control"], "no-store")
        failed = self.logs().filtered(lambda log: log.result != "success")
        self.assertEqual(failed.remote_addr, "192.0.2.10")

    def test_a_missing_password_is_a_wrong_password(self):
        self._call("create_folio_payment_card", body=VISA)

        body, _headers, status = self._call("delete_folio_payment_card")

        self.assertEqual((status, body["code"]), (422, "invalid_card_password"))

    def test_details_and_edit(self):
        self._call("create_folio_payment_card", body=VISA)

        body, headers, status = self._call(
            "get_folio_payment_card_details", password=PASSWORD
        )
        self.assertEqual((status, body["pan"]), (200, "4242424242424242"))
        self.assertEqual(headers["Cache-Control"], "no-store")

        body, _headers, status = self._call(
            "update_folio_payment_card", password=PASSWORD, body={"holder": "J. Doe"}
        )
        self.assertEqual((status, body["card"]["holder"]), (200, "J. Doe"))

    def test_a_body_that_is_not_json(self):
        body, _headers, status = self._call(
            "create_folio_payment_card", body=ValueError("not json")
        )

        self.assertEqual((status, body["code"]), (422, "validation_error"))
