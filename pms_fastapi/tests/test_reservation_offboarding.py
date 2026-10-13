import datetime

from fastapi import status

from odoo import fields

from odoo.addons.pms_fastapi.tests.common import CommonTestPmsApi


class TestReservationOffboarding(CommonTestPmsApi):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.room_type_class = cls.env["pms.room.type.class"].create(
            {
                "name": "Standard",
                "default_code": "STD",
            }
        )
        cls.room_type = cls.env["pms.room.type"].create(
            {
                "pms_property_ids": [cls.test_property.id],
                "name": "Double Test",
                "default_code": "DBL_Test",
                "class_id": cls.room_type_class.id,
            }
        )
        cls.room = cls.env["pms.room"].create(
            {
                "pms_property_id": cls.test_property.id,
                "name": "101",
                "room_type_id": cls.room_type.id,
                "capacity": 2,
            }
        )
        cls.partner = cls.env["res.partner"].create(
            {
                "firstname": "John",
                "lastname": "Doe",
                "email": "john@example.com",
            }
        )
        cls.sale_channel = cls.env["pms.sale.channel"].create(
            {"name": "Direct Test", "channel_type": "direct"}
        )

    def _create_in_house_reservation(self, checkin, checkout, adults=2):
        folio = self.env["pms.folio"].create(
            {
                "pms_property_id": self.test_property.id,
                "partner_name": self.partner.name,
                "partner_id": self.partner.id,
            }
        )
        reservation = self.env["pms.reservation"].create(
            {
                "folio_id": folio.id,
                "room_type_id": self.room_type.id,
                "preferred_room_id": self.room.id,
                "partner_id": self.partner.id,
                "adults": adults,
                "sale_channel_origin_id": self.sale_channel.id,
                "reservation_line_ids": [
                    (0, False, {"date": checkin + datetime.timedelta(days=i)})
                    for i in range((checkout - checkin).days)
                ],
            }
        )
        reservation.checkin_partner_ids.update(
            {"state": "onboard", "arrival": fields.Datetime.now()}
        )
        reservation.state = "onboard"
        self.room.cleaning_status = "clean"
        return reservation

    def _post_offboarding(self, reservation):
        with self._create_test_client() as test_client:
            self._login(test_client)
            response = test_client.post(f"/reservations/{reservation.id}/offboarding")
        self.assertEqual(
            response.status_code, status.HTTP_204_NO_CONTENT, response.text
        )

    def test_offboarding_on_checkout_day_checks_out_reservation(self):
        """On checkout day the whole stay is closed, not only its guests."""
        today = fields.Date.today()
        reservation = self._create_in_house_reservation(
            today - datetime.timedelta(days=1), today
        )

        self._post_offboarding(reservation)

        self.assertEqual(reservation.state, "done")
        self.assertEqual(set(reservation.checkin_partner_ids.mapped("state")), {"done"})
        self.assertEqual(self.room.cleaning_status, "dirty")

    def test_offboarding_closes_reservation_whose_guests_already_left(self):
        """A delayed departure with no guest in-house can still be closed."""
        today = fields.Date.today()
        reservation = self._create_in_house_reservation(
            today - datetime.timedelta(days=1), today
        )
        reservation.checkin_partner_ids.action_done()
        reservation.state = "departure_delayed"

        self._post_offboarding(reservation)

        self.assertEqual(reservation.state, "done")
        self.assertEqual(self.room.cleaning_status, "dirty")

    def test_offboarding_before_checkout_day_only_marks_guests(self):
        """Before checkout day only the guests leave; the stay stays open."""
        today = fields.Date.today()
        reservation = self._create_in_house_reservation(
            today, today + datetime.timedelta(days=2)
        )

        self._post_offboarding(reservation)

        self.assertEqual(reservation.state, "onboard")
        self.assertEqual(set(reservation.checkin_partner_ids.mapped("state")), {"done"})
        self.assertEqual(self.room.cleaning_status, "clean")
