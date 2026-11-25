import datetime

from fastapi import status

from odoo import fields

from .common import CommonTestCustomerPortalApi

FOLIO_KEYS = {
    "id",
    "code",
    "state",
    "reservationIds",
    "customer",
    "checkInDate",
    "checkOutDate",
    "arrivalHour",
    "departureHour",
    "adults",
    "children",
    "completedGuests",
    "currency",
    "totalAmount",
    "pendingAmount",
    "paymentState",
    "saleLines",
}


class TestPortalFolio(CommonTestCustomerPortalApi):
    def _get_folio(self, token):
        return self._get(f"/portal/{token}/folio")

    def _assert_not_found(self, response):
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(response.headers["content-type"], "application/problem+json")
        body = response.json()
        self.assertEqual(body["type"], "/errors/portal-link-not-found")
        self.assertEqual(body["status"], 404)

    def test_folio_by_token(self):
        folio = self._create_folio(
            [(self.double, 3, 2, 2, 1), (self.single, 4, 3, 1, 0)]
        )
        double, single = folio.reservation_ids.sorted("checkin")
        double.write({"arrival_hour": "16:30", "departure_hour": "10:00"})
        single.write({"arrival_hour": "20:00", "departure_hour": "11:15"})
        today = fields.Date.today()

        response = self._get_folio(folio.access_token)

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
        body = response.json()
        self.assertEqual(set(body), FOLIO_KEYS)
        self.assertEqual(body["id"], folio.id)
        self.assertEqual(body["code"], folio.name)
        self.assertEqual(body["state"], "confirmed")
        self.assertEqual(body["reservationIds"], folio.reservation_ids.ids)
        self.assertEqual(
            body["customer"],
            {
                "id": self.partner.id,
                "firstName": "Laura",
                "lastName": "García",
                "lastName2": "Pérez",
                "gender": "female",
                "email": "laura@example.com",
                "phone": "+34600000000",
            },
        )
        self.assertEqual(body["checkInDate"], str(today + datetime.timedelta(days=3)))
        self.assertEqual(body["checkOutDate"], str(today + datetime.timedelta(days=7)))
        self.assertEqual(body["arrivalHour"], "16:30")
        self.assertEqual(body["departureHour"], "11:15")
        self.assertEqual(body["adults"], 3)
        self.assertEqual(body["children"], 1)
        self.assertEqual(body["completedGuests"], 0)
        self.assertEqual(
            body["currency"],
            {
                "id": folio.currency_id.id,
                "code": folio.currency_id.name,
                "name": folio.currency_id.full_name,
            },
        )
        self.assertEqual(body["totalAmount"], 190.0)
        self.assertEqual(body["pendingAmount"], 190.0)
        self.assertEqual(body["paymentState"], "notPaid")
        self.assertEqual(
            body["saleLines"],
            [
                {"name": "Double Portal", "amount": 100.0},
                {"name": "Single Portal", "amount": 90.0},
            ],
        )

    def test_unknown_token_is_not_found(self):
        self._create_folio([(self.double, 3, 2, 2, 0)])

        self._assert_not_found(self._get_folio("not-a-real-token"))

    def test_quotation_folio_is_not_found(self):
        folio = self._create_folio([(self.double, 3, 2, 2, 0)])
        for state in ("draft", "sent"):
            with self.subTest(state=state):
                folio.write({"state": state})

                self._assert_not_found(self._get_folio(folio.access_token))

    def test_folio_state_mapping(self):
        folio = self._create_folio([(self.double, 3, 2, 2, 0)])
        mapping = {"confirm": "confirmed", "cancel": "cancelled", "done": "done"}
        for odoo_state, api_state in mapping.items():
            with self.subTest(state=odoo_state):
                folio.write({"state": odoo_state})

                response = self._get_folio(folio.access_token)

                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["state"], api_state)

    def test_payment_state_mapping(self):
        folio = self._create_folio([(self.double, 3, 2, 2, 0)])
        mapping = {
            "not_paid": "notPaid",
            "partial": "partiallyPaid",
            "paid": "paid",
            "overpayment": "overpaid",
            "nothing_to_pay": "nothingToPay",
        }
        for odoo_state, api_state in mapping.items():
            with self.subTest(payment_state=odoo_state):
                folio.write({"payment_state": odoo_state})

                response = self._get_folio(folio.access_token)

                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["paymentState"], api_state)

    def test_stay_ignores_cancelled_reservations(self):
        folio = self._create_folio(
            [
                (self.double, 2, 1, 2, 2),
                (self.double, 3, 2, 2, 1),
                (self.single, 4, 3, 1, 0),
                (self.single, 5, 4, 1, 0),
            ]
        )
        first, second, third, last = folio.reservation_ids.sorted("checkin")
        first.write({"arrival_hour": "09:00"})
        second.write({"arrival_hour": "16:30"})
        third.write({"departure_hour": "11:15"})
        last.write({"departure_hour": "13:00"})
        (first | last).action_cancel()

        body = self._get_folio(folio.access_token).json()

        self.assertEqual(body["arrivalHour"], "16:30")
        self.assertEqual(body["departureHour"], "11:15")
        self.assertEqual(body["adults"], 3)
        self.assertEqual(body["children"], 1)

    def test_completed_guests(self):
        folio = self._create_folio(
            [(self.double, 3, 2, 4, 0), (self.single, 3, 2, 2, 0)]
        )
        double, single = folio.reservation_ids.sorted("room_type_id")
        states = ("precheckin", "onboard", "done", "draft")
        for guest, state in zip(double.checkin_partner_ids, states, strict=True):
            guest.write({"state": state})
        single.checkin_partner_ids[:1].write({"state": "precheckin"})
        single.action_cancel()

        body = self._get_folio(folio.access_token).json()

        self.assertEqual(body["completedGuests"], 3)

    def test_customer_without_partner(self):
        folio = self._create_folio(
            [(self.double, 3, 2, 2, 0)],
            partner=self.env["res.partner"],
            folio_values={
                "partner_name": "Walk-in Guest",
                "email": "walkin@example.com",
                "mobile": "+34611111111",
            },
        )

        body = self._get_folio(folio.access_token).json()

        self.assertEqual(
            body["customer"],
            {
                "id": None,
                "firstName": "Walk-in Guest",
                "lastName": "",
                "lastName2": None,
                "gender": None,
                "email": "walkin@example.com",
                "phone": "+34611111111",
            },
        )

    def test_sale_lines_aggregated_for_display(self):
        parking = self.env["product.product"].create(
            {"name": "Parking", "list_price": 13.0, "per_day": False}
        )
        tourist_tax = self.env["product.product"].create(
            {
                "name": "Tourist tax",
                "list_price": 0.5,
                "is_tourist_tax": True,
                "per_person": True,
                "tourist_tax_date_start": "01-01",
                "tourist_tax_date_end": "12-31",
            }
        )
        folio = self._create_folio(
            [
                (self.double, 3, 2, 2, 0),
                (self.single, 3, 2, 1, 0),
                (self.double, 3, 2, 2, 0),
            ]
        )
        double1, double2 = folio.reservation_ids.filtered(
            lambda r: r.room_type_id == self.double
        )
        for reservation in (double1, double2):
            self.env["pms.service"].create(
                {"reservation_id": reservation.id, "product_id": parking.id}
            )
        double1.reservation_line_ids[:1].write({"discount": 10.0})
        tax_lines = folio.sale_line_ids.filtered(
            lambda line: line.product_id == tourist_tax
        )
        self.assertTrue(tax_lines, "The fixture should generate a tourist tax")

        body = self._get_folio(folio.access_token).json()

        self.assertEqual(
            body["saleLines"],
            [
                {"name": "Double Portal", "amount": 200.0},
                {"name": "Single Portal", "amount": 60.0},
                {"name": "Parking", "amount": 26.0},
                {"name": "Discounts", "amount": -5.0},
                {
                    "name": "Tourist tax",
                    "amount": sum(tax_lines.mapped("price_total")),
                },
            ],
        )
        self.assertAlmostEqual(
            sum(line["amount"] for line in body["saleLines"]),
            body["totalAmount"],
            places=2,
        )

    def test_sections_and_down_payments_are_not_listed(self):
        folio = self._create_folio([(self.double, 3, 2, 2, 0)])
        self.assertTrue(
            folio.sale_line_ids.filtered("display_type"),
            "The folio should have a section line per reservation",
        )
        self.env["folio.sale.line"].create(
            {
                "folio_id": folio.id,
                "name": "Down payment",
                "is_downpayment": True,
                "price_unit": 0.0,
                "product_uom_qty": 0.0,
            }
        )

        body = self._get_folio(folio.access_token).json()

        self.assertEqual(
            body["saleLines"], [{"name": "Double Portal", "amount": 100.0}]
        )
