# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
from datetime import timedelta
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from odoo import fields
from odoo.tests import tagged

from .common import BookingEngineCase

RETURN_URL = "https://booking.example.com/confirmation"
REDIRECT = {
    "type": "redirect",
    "method": "POST",
    "url": "https://gateway.example.com/pay",
    "fields": {"order": "1"},
}


@tagged("post_install", "-at_install")
class TestCheckout(BookingEngineCase):
    """Prices of the common case: the double is 100 a night (90 non
    refundable), the single 60 (54); the tourist tax is 1.5 per adult and
    night. The non refundable rooms are paid now with their tourist tax, the
    rest at the property."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.tourist_tax.list_price = 1.5
        cls.pms_property.booking_engine_allowed_origins = (
            "https://booking.example.com\nhttp://localhost:5173"
        )
        journal = cls.env["account.journal"].create(
            {"name": "Gateway", "type": "bank", "code": "BEGW"}
        )
        cls.provider = cls.env["payment.provider"].create(
            {
                "name": "Card gateway",
                "code": "none",
                "state": "test",
                "is_published": True,
                "company_id": cls.env.company.id,
                "journal_id": journal.id,
                "pms_property_ids": [(6, 0, cls.pms_property.ids)],
            }
        )
        provider = cls.provider
        cls.startClassPatcher(
            patch.object(
                type(cls.env["payment.provider"]),
                "_get_booking_engine_payment_method",
                lambda self: "card" if self == provider else None,
            )
        )
        cls.startClassPatcher(
            patch.object(
                type(cls.env["payment.transaction"]),
                "_get_booking_engine_next_action",
                lambda self: dict(REDIRECT),
            )
        )

    def _selection(self, rooms, adults=2, children=0):
        return {
            "checkIn": self.check_in.isoformat(),
            "checkOut": self.check_out.isoformat(),
            "adults": adults,
            "children": children,
            "rooms": [
                {"offerId": offer_id, "quantity": quantity}
                for offer_id, quantity in rooms
            ],
            "extras": [],
        }

    def _preview(self, rooms, **kwargs):
        with self._create_test_client() as client:
            return client.post(
                f"/properties/{self.pms_property.id}/bookings/preview",
                json=self._selection(rooms, **kwargs),
            )

    def _booking_request(self, rooms, total, due_now, **kwargs):
        body = self._selection(rooms)
        body.update(
            {
                "holder": {
                    "firstName": "Lucía",
                    "lastName": "Pérez Rey",
                    "email": "lucia@example.com",
                    "phone": "+34600111222",
                    "country": "ES",
                },
                "arrivalTime": "18:30",
                "requests": "Cot, if possible",
                "language": "en",
                "marketingOptIn": False,
                "termsAccepted": True,
                "paymentMethod": "card" if due_now else None,
                "returnUrl": RETURN_URL,
                "expected": {"total": total, "dueNow": due_now},
            }
        )
        body.update(kwargs)
        return body

    def _create(self, body, key="key-1"):
        with self._create_test_client() as client:
            return client.post(
                f"/properties/{self.pms_property.id}/bookings",
                json=body,
                headers={"Idempotency-Key": key} if key else {},
            )

    def _get(self, booking_id, token):
        with self._create_test_client() as client:
            return client.get(
                f"/bookings/{booking_id}",
                headers={"Authorization": f"Bearer {token}"},
            )

    def _booking(self, response):
        return self.env["pms.booking.engine.booking"].search(
            [("public_id", "=", response.json()["booking"]["id"])]
        )

    def _flexible_double(self):
        return self._offer_id(self.pricelist_flexible, self.double)

    def _non_refundable_double(self):
        return self._offer_id(self.pricelist_non_refundable, self.double)

    # Preview

    def test_preview_flexible_pays_at_property(self):
        response = self._preview([(self._flexible_double(), 1)])

        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(data["stay"]["nights"], 2)
        (room,) = data["rooms"]
        self.assertEqual(room["roomTypeId"], str(self.double.id))
        self.assertEqual((room["adults"], room["children"]), (2, 0))
        self.assertEqual(room["price"]["total"], 200.0)
        self.assertEqual(
            data["price"],
            {
                "currency": self.pricelist_flexible.currency_id.name,
                "rooms": 200.0,
                "extras": 0.0,
                "touristTax": 6.0,
                "discount": 0.0,
                "total": 206.0,
            },
        )
        self.assertEqual(
            data["payment"], {"dueNow": 0.0, "later": [], "atProperty": 206.0}
        )
        self.assertEqual(data["paymentMethods"], [])
        self.assertEqual(data["ratePlans"][0]["cancellation"]["type"], "free")

    def test_preview_non_refundable_pays_now(self):
        response = self._preview([(self._non_refundable_double(), 1)])

        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        price = data["rooms"][0]["price"]
        self.assertEqual(price["total"], 180.0)
        self.assertEqual(price["original"], 200.0)
        self.assertEqual(price["discount"]["percent"], 10.0)
        self.assertEqual(data["price"]["discount"], 20.0)
        self.assertEqual(data["price"]["total"], 186.0)
        # The tourist tax is paid with the room
        self.assertEqual(
            data["payment"], {"dueNow": 186.0, "later": [], "atProperty": 0.0}
        )
        self.assertEqual(data["paymentMethods"], ["card"])

    def test_preview_without_provider_pays_at_property(self):
        self.provider.state = "disabled"

        response = self._preview([(self._non_refundable_double(), 1)])

        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(
            data["payment"], {"dueNow": 0.0, "later": [], "atProperty": 186.0}
        )
        self.assertEqual(data["paymentMethods"], [])

    def test_preview_distributes_guests(self):
        response = self._preview(
            [
                (self._flexible_double(), 1),
                (self._offer_id(self.pricelist_non_refundable, self.single), 1),
            ],
            adults=3,
        )

        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(
            [(room["adults"], room["price"]["total"]) for room in data["rooms"]],
            [(2, 200.0), (1, 108.0)],
        )
        self.assertEqual(data["price"]["touristTax"], 9.0)
        self.assertEqual(data["price"]["total"], 317.0)
        # The non refundable single, with its tourist tax
        self.assertEqual(data["payment"]["dueNow"], 111.0)
        self.assertEqual(data["payment"]["atProperty"], 206.0)
        self.assertEqual(len(data["ratePlans"]), 2)

    def test_preview_offer_unavailable(self):
        unknown = self._offer_id(self.pricelist_flexible, self.suite)

        response = self._preview([(unknown, 1)])

        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["type"], "/errors/offer-unavailable")
        self.assertEqual(response.json()["offerIds"], [unknown])

        # The property has one single room
        single = self._offer_id(self.pricelist_flexible, self.single)
        response = self._preview([(single, 2)], adults=2)

        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["offerIds"], [single])

        response = self._preview([("not-an-offer", 1)])

        self.assertEqual(response.status_code, 409, response.text)

    def test_preview_occupancy_not_covered(self):
        response = self._preview([(self._flexible_double(), 2)], adults=1)

        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(response.json()["type"], "/errors/occupancy-not-covered")

        response = self._preview([(self._flexible_double(), 1)], adults=3)

        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(response.json()["type"], "/errors/occupancy-not-covered")

    def test_preview_extras_not_available(self):
        body = self._selection([(self._flexible_double(), 1)])
        body["extras"] = [{"extraId": "parking", "quantity": 1}]

        with self._create_test_client() as client:
            response = client.post(
                f"/properties/{self.pms_property.id}/bookings/preview", json=body
            )

        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(response.json()["extraIds"], ["parking"])

    # Creation

    def test_create_without_payment_confirms(self):
        response = self._create(
            self._booking_request([(self._flexible_double(), 1)], 206.0, 0.0)
        )

        self.assertEqual(response.status_code, 201, response.text)
        data = response.json()
        self.assertEqual(data["nextAction"], {"type": "none"})
        self.assertEqual(data["booking"]["status"], "confirmed")
        self.assertIsNone(data["booking"]["expiresAt"])
        self.assertTrue(data["accessToken"])
        self.assertTrue(
            response.headers["Location"].endswith(f"/bookings/{data['booking']['id']}")
        )
        booking = self._booking(response)
        folio = booking.folio_id
        self.assertEqual(data["booking"]["code"], folio.name)
        self.assertEqual(folio.sale_channel_origin_id, self.engine_channel)
        self.assertEqual(folio.partner_name, "Lucía Pérez Rey")
        self.assertEqual(folio.email, "lucia@example.com")
        reservation = folio.reservation_ids
        self.assertEqual(reservation.state, "confirm")
        self.assertEqual(reservation.pricelist_id, self.pricelist_flexible)
        self.assertFalse(reservation.board_service_room_id)
        self.assertEqual(reservation.arrival_hour, "18:30")
        self.assertTrue(
            reservation.service_ids.filtered(lambda s: s.product_id.is_tourist_tax)
        )
        self.assertAlmostEqual(folio.amount_total, 206.0)
        self.assertTrue(booking.terms_accepted_at)

    def test_create_replay_and_key_reused(self):
        body = self._booking_request([(self._flexible_double(), 1)], 206.0, 0.0)
        first = self._create(body)

        replay = self._create(body)

        self.assertEqual(replay.status_code, 201, replay.text)
        self.assertEqual(replay.json()["booking"]["id"], first.json()["booking"]["id"])
        self.assertEqual(replay.json()["accessToken"], first.json()["accessToken"])
        self.assertEqual(
            self.env["pms.booking.engine.booking"].search_count(
                [("idempotency_key", "=", "key-1")]
            ),
            1,
        )

        body["requests"] = "Another one"
        reused = self._create(body)

        self.assertEqual(reused.status_code, 400, reused.text)
        self.assertEqual(reused.json()["type"], "/errors/idempotency-key-reused")

    def test_create_rejections(self):
        rooms = [(self._flexible_double(), 1)]

        response = self._create(self._booking_request(rooms, 206.0, 0.0), key=None)
        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(response.json()["field"], "Idempotency-Key")

        response = self._create(
            self._booking_request(rooms, 206.0, 0.0, termsAccepted=False), key="k2"
        )
        self.assertEqual(response.json()["type"], "/errors/terms-not-accepted")

        response = self._create(
            self._booking_request(
                rooms, 206.0, 0.0, returnUrl="https://evil.example.com/x"
            ),
            key="k3",
        )
        self.assertEqual(response.json()["type"], "/errors/return-url-not-allowed")

        response = self._create(self._booking_request(rooms, 199.0, 0.0), key="k4")
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["type"], "/errors/price-changed")
        self.assertEqual(response.json()["preview"]["price"]["total"], 206.0)

        response = self._create(
            self._booking_request(rooms, 206.0, 0.0, paymentMethod="card"), key="k5"
        )
        self.assertEqual(
            response.json()["type"], "/errors/payment-method-not-available"
        )
        self.assertFalse(
            self.env["pms.booking.engine.booking"].search(
                [("pms_property_id", "=", self.pms_property.id)]
            )
        )

    def test_create_with_payment_holds_until_paid(self):
        response = self._create(
            self._booking_request([(self._non_refundable_double(), 1)], 186.0, 186.0)
        )

        self.assertEqual(response.status_code, 201, response.text)
        data = response.json()
        self.assertEqual(data["nextAction"], REDIRECT)
        self.assertEqual(data["booking"]["status"], "pendingPayment")
        self.assertTrue(data["booking"]["expiresAt"])
        booking = self._booking(response)
        self.assertEqual(booking.folio_id.reservation_ids.state, "draft")
        transaction = booking.transaction_ids
        self.assertEqual(transaction.amount, 186.0)
        self.assertEqual(transaction.folio_ids, booking.folio_id)
        self.assertLessEqual(len(transaction.reference), 12)
        self.assertTrue(transaction.reference[:4].isdigit())

        pending = self._get(data["booking"]["id"], data["accessToken"]).json()
        self.assertEqual(pending["payment"]["lastAttempt"], {"status": "pending"})
        self.assertEqual(pending["payment"]["paid"], 0.0)

        transaction._set_done()
        # The test provider has no payment method in accounting: the payment
        # itself is checked with a real gateway, in its bridge module
        with patch.object(
            type(self.env["payment.transaction"]),
            "_create_payment",
            lambda tx: self.env["account.payment"],
        ):
            transaction._finalize_post_processing()

        self.assertEqual(booking.state, "confirmed")
        reservation = booking.folio_id.reservation_ids
        self.assertEqual(reservation.state, "confirm")
        self.assertTrue(
            reservation.service_ids.filtered(lambda s: s.product_id.is_tourist_tax)
        )
        confirmed = self._get(data["booking"]["id"], data["accessToken"]).json()
        self.assertEqual(confirmed["status"], "confirmed")
        self.assertIsNone(confirmed["expiresAt"])
        self.assertEqual(
            confirmed["payment"],
            {
                "paid": 186.0,
                "later": [],
                "atProperty": 0.0,
                "method": "card",
                "card": None,
                "lastAttempt": None,
            },
        )

    def test_get_booking_needs_its_token(self):
        response = self._create(
            self._booking_request([(self._flexible_double(), 1)], 206.0, 0.0)
        )
        booking_id = response.json()["booking"]["id"]

        self.assertEqual(self._get(booking_id, "wrong").status_code, 404)
        self.assertEqual(self._get("bk_unknown", "wrong").status_code, 404)
        data = self._get(booking_id, response.json()["accessToken"]).json()
        self.assertEqual(data["holder"]["firstName"], "Lucía")
        self.assertEqual(data["property"]["name"], self.pms_property.name)
        self.assertEqual(data["price"]["total"], 206.0)

    def test_expired_hold_is_released_without_penalty(self):
        response = self._create(
            self._booking_request([(self._non_refundable_double(), 1)], 186.0, 186.0)
        )
        booking = self._booking(response)
        booking.expires_at = fields.Datetime.now() - timedelta(minutes=1)

        self.env["pms.booking.engine.booking"]._cron_expire_holds()

        self.assertEqual(booking.state, "expired")
        reservation = booking.folio_id.reservation_ids
        self.assertEqual(reservation.state, "cancel")
        self.assertFalse(reservation.service_ids.filtered("is_cancel_penalty"))
        data = self._get(
            response.json()["booking"]["id"], response.json()["accessToken"]
        ).json()
        self.assertEqual(data["status"], "expired")
        self.assertIsNone(data["expiresAt"])

    def test_payment_in_progress_keeps_the_hold(self):
        response = self._create(
            self._booking_request([(self._non_refundable_double(), 1)], 186.0, 186.0)
        )
        booking = self._booking(response)
        booking.expires_at = fields.Datetime.now() - timedelta(minutes=1)
        booking.transaction_ids._set_pending()

        self.env["pms.booking.engine.booking"]._cron_expire_holds()

        self.assertEqual(booking.state, "pending_payment")

    def test_retry_payment(self):
        response = self._create(
            self._booking_request([(self._non_refundable_double(), 1)], 186.0, 186.0)
        )
        data = response.json()
        booking = self._booking(response)
        booking.transaction_ids._set_error("Denied")
        failed = self._get(data["booking"]["id"], data["accessToken"]).json()
        self.assertEqual(failed["payment"]["lastAttempt"], {"status": "failed"})

        with self._create_test_client() as client:
            retry = client.post(
                f"/bookings/{data['booking']['id']}/payments",
                json={"paymentMethod": "card", "returnUrl": RETURN_URL},
                headers={
                    "Authorization": f"Bearer {data['accessToken']}",
                    "Idempotency-Key": "retry-1",
                },
            )

        self.assertEqual(retry.status_code, 201, retry.text)
        self.assertEqual(retry.json()["nextAction"], REDIRECT)
        self.assertEqual(len(booking.transaction_ids), 2)
        self.assertNotEqual(*booking.transaction_ids.mapped("reference"))

        booking.state = "expired"
        with self._create_test_client() as client:
            refused = client.post(
                f"/bookings/{data['booking']['id']}/payments",
                json={"paymentMethod": "card", "returnUrl": RETURN_URL},
                headers={
                    "Authorization": f"Bearer {data['accessToken']}",
                    "Idempotency-Key": "retry-2",
                },
            )
        self.assertEqual(refused.status_code, 409, refused.text)
        self.assertEqual(refused.json()["bookingStatus"], "expired")

    def test_payment_return_redirects_to_the_engine(self):
        response = self._create(
            self._booking_request([(self._non_refundable_double(), 1)], 186.0, 186.0)
        )
        booking_id = response.json()["booking"]["id"]

        with self._create_test_client() as client:
            back = client.get(
                f"/bookings/{booking_id}/payment-return",
                params={"result": "ko"},
                follow_redirects=False,
            )

        self.assertEqual(back.status_code, 303, back.text)
        location = urlsplit(back.headers["location"])
        self.assertEqual(
            f"{location.scheme}://{location.netloc}{location.path}", RETURN_URL
        )
        self.assertEqual(
            parse_qs(location.query), {"bookingId": [booking_id], "payment": ["ko"]}
        )
