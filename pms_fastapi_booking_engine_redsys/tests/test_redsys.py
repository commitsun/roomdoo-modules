# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
import base64
import json
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

from odoo import fields
from odoo.tests import tagged

from odoo.addons.pms_fastapi_booking_engine.tests.common import BookingEngineCase

RETURN_URL = "https://booking.example.com/confirmation"
# Public test merchant of Redsys
TEST_MERCHANT = {
    "redsys_merchant_code": "999008881",
    "redsys_terminal": "1",
    "redsys_secret_key": "sq7HjrUOBfKmC576ILgskD5srU870gJ7",
    "redsys_merchant_name": "Booking Engine Hotel",
    "redsys_merchant_description": "Booking",
}


@tagged("post_install", "-at_install")
class TestBookingEngineRedsys(BookingEngineCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.pms_property.booking_engine_allowed_origins = "https://booking.example.com"
        journal = cls.env["account.journal"].create(
            {"name": "Redsys", "type": "bank", "code": "BERS"}
        )
        common = {
            "code": "redsys",
            "state": "test",
            "is_published": True,
            "company_id": cls.env.company.id,
            "journal_id": journal.id,
            "pms_property_ids": [(6, 0, cls.pms_property.ids)],
            "redirect_form_view_id": cls.env.ref("payment_redsys.redsys_form").id,
            **TEST_MERCHANT,
        }
        cls.card = cls.env["payment.provider"].create(
            dict(common, name="Redsys card", redsys_pay_method="T")
        )
        cls.bizum = cls.env["payment.provider"].create(
            dict(common, name="Redsys Bizum", redsys_pay_method="z", sequence=20)
        )
        cls.offer_id = cls._offer_id(cls.pricelist_non_refundable, cls.double)

    def _create(self, method="card"):
        body = {
            "checkIn": self.check_in.isoformat(),
            "checkOut": self.check_out.isoformat(),
            "adults": 2,
            "children": 0,
            "rooms": [{"offerId": self.offer_id, "quantity": 1}],
            "extras": [],
            "holder": {
                "firstName": "Lucía",
                "lastName": "Pérez Rey",
                "email": "lucia@example.com",
                "phone": "+34600111222",
                "country": "ES",
            },
            "language": "es",
            "termsAccepted": True,
            "paymentMethod": method,
            "returnUrl": RETURN_URL,
            "expected": {"total": 186.0, "dueNow": 186.0},
        }
        with self._create_test_client() as client:
            response = client.post(
                f"/properties/{self.pms_property.id}/bookings",
                json=body,
                headers={"Idempotency-Key": f"redsys-{method}"},
            )
        self.assertEqual(response.status_code, 201, response.text)
        booking = self.env["pms.booking.engine.booking"].search(
            [("public_id", "=", response.json()["booking"]["id"])]
        )
        return response.json(), booking

    def _notification(self, transaction, response_code):
        parameters = base64.b64encode(
            json.dumps(
                {
                    "Ds_Order": transaction.reference,
                    "Ds_Response": response_code,
                    "Ds_AuthorisationCode": "123456",
                }
            ).encode()
        ).decode()
        return {
            "Ds_SignatureVersion": "HMAC_SHA256_V1",
            "Ds_MerchantParameters": parameters,
            "Ds_Signature": transaction.provider_id.sign_parameters(
                transaction.provider_id.redsys_secret_key, parameters
            ),
        }

    def test_payment_methods(self):
        self.assertEqual(self.card._get_booking_engine_payment_method(), "card")
        self.assertEqual(self.bizum._get_booking_engine_payment_method(), "bizum")
        self.card.redsys_pay_method = "R"
        self.assertIsNone(self.card._get_booking_engine_payment_method())

    def test_preview_offers_card_and_bizum(self):
        with self._create_test_client() as client:
            response = client.post(
                f"/properties/{self.pms_property.id}/bookings/preview",
                json={
                    "checkIn": self.check_in.isoformat(),
                    "checkOut": self.check_out.isoformat(),
                    "adults": 2,
                    "children": 0,
                    "rooms": [{"offerId": self.offer_id, "quantity": 1}],
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["paymentMethods"], ["card", "bizum"])

    def test_gateway_sends_the_guest_back_to_the_engine(self):
        data, booking = self._create("bizum")

        action = data["nextAction"]
        self.assertEqual(action["type"], "redirect")
        self.assertEqual(action["method"], "POST")
        self.assertIn("sis-t.redsys.es", action["url"])
        fields = action["fields"]
        transaction = booking.transaction_ids
        self.assertEqual(transaction.provider_id, self.bizum)
        parameters = json.loads(base64.b64decode(fields["Ds_MerchantParameters"]))
        self.assertEqual(parameters["Ds_Merchant_Order"], transaction.reference)
        self.assertEqual(parameters["Ds_Merchant_Amount"], "18600")
        self.assertEqual(parameters["Ds_Merchant_Paymethods"], "z")
        self.assertEqual(parameters["Ds_Merchant_Titular"], "Lucía Pérez Rey")
        for result in ("ok", "ko"):
            url = urlsplit(parameters[f"Ds_Merchant_Url{result.capitalize()}"])
            self.assertEqual(
                url.path,
                f"/booking-engine/v1/bookings/{booking.public_id}/payment-return",
            )
            self.assertEqual(parse_qs(url.query), {"result": [result]})
        self.assertEqual(
            fields["Ds_Signature"],
            self.bizum.sign_parameters(
                self.bizum.redsys_secret_key, fields["Ds_MerchantParameters"]
            ),
        )

    def test_notification_confirms_and_cannot_undo(self):
        _data, booking = self._create()
        transaction = booking.transaction_ids
        Transaction = self.env["payment.transaction"].sudo()

        Transaction._handle_notification_data(
            "redsys", self._notification(transaction, "0000")
        )

        self.assertEqual(transaction.state, "done")
        self.assertEqual(booking.state, "confirmed")
        self.assertTrue(transaction.payment_id)
        # Paid in full, tourist tax included
        self.assertAlmostEqual(booking.folio_id.pending_amount, 0.0)

        Transaction._handle_notification_data(
            "redsys", self._notification(transaction, "0190")
        )

        self.assertEqual(transaction.state, "done")

    def test_denied_payment_fails_and_releases_the_hold(self):
        """Redsys denials (0101 to 0203) are left pending by the Redsys
        module: the engine fails them, so the guest can retry and the hold is
        released if nobody pays."""
        data, booking = self._create()
        transaction = booking.transaction_ids

        self.env["payment.transaction"].sudo()._handle_notification_data(
            "redsys", self._notification(transaction, "0190")
        )

        self.assertEqual(transaction.state, "error")
        with self._create_test_client() as client:
            status = client.get(
                f"/bookings/{booking.public_id}",
                headers={"Authorization": f"Bearer {data['accessToken']}"},
            ).json()
        self.assertEqual(status["payment"]["lastAttempt"], {"status": "failed"})
        booking.expires_at = fields.Datetime.now() - timedelta(minutes=1)
        self.env["pms.booking.engine.booking"]._cron_expire_holds()
        self.assertEqual(booking.state, "expired")

    def test_return_with_the_result_confirms(self):
        """Some terminals never notify the server: the result the guest
        brings back is enough."""
        data, booking = self._create()
        params = dict(self._notification(booking.transaction_ids, "0000"), result="ok")

        with self._create_test_client() as client:
            back = client.get(
                f"/bookings/{booking.public_id}/payment-return",
                params=params,
                follow_redirects=False,
            )

        self.assertEqual(back.status_code, 303, back.text)
        self.assertTrue(back.headers["location"].startswith(RETURN_URL))
        self.assertEqual(booking.state, "confirmed")
        self.assertEqual(data["booking"]["status"], "pendingPayment")
