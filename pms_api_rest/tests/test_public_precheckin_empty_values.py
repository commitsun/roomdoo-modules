# Copyright 2026 Commit [Sun]
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
"""Odoo reads an empty field as False, and the answer of the public pre-check-in
pages only takes text in its text fields: a hotel without a phone turned the
page of each of its guests into a server error.
"""
import datetime

from odoo.tests import HttpCase, tagged

from odoo.addons.pms.tests.common import TestPms


@tagged("post_install", "-at_install")
class TestPublicPrecheckinEmptyValues(TestPms, HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.pms_property1.phone = False
        room_type = cls.env["pms.room.type"].create(
            {
                "pms_property_ids": [cls.pms_property1.id],
                "name": "Double",
                "default_code": "DBL",
                "class_id": cls.room_type_class1.id,
            }
        )
        cls.env["pms.room"].create(
            {
                "pms_property_id": cls.pms_property1.id,
                "name": "Room 101",
                "room_type_id": room_type.id,
                "capacity": 2,
            }
        )
        # An out of service block is a folio without a customer name
        cls.reservation = cls.env["pms.reservation"].create(
            {
                "pms_property_id": cls.pms_property1.id,
                "checkin": datetime.date.today(),
                "checkout": datetime.date.today() + datetime.timedelta(days=2),
                "adults": 1,
                "room_type_id": room_type.id,
                "partner_name": False,
                "reservation_type": "out",
                "closure_reason_id": cls.env["room.closure.reason"]
                .create({"name": "Works"})
                .id,
                "sale_channel_origin_id": cls.env["pms.sale.channel"]
                .create({"name": "Door", "channel_type": "direct"})
                .id,
            }
        )
        cls.folio = cls.reservation.folio_id

    def _get(self, path):
        # The request reads the database, not the cache of this test
        self.env.flush_all()
        response = self.url_open(path)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_folio_page(self):
        token = self.folio._portal_ensure_token()
        data = self._get(f"/api/folios/{self.folio.id}/precheckin/{token}")
        self.assertEqual(data["pmsPropertyPhoneNumber"], "")
        self.assertEqual(data["folioPartnerName"], "")

    def test_reservation_page(self):
        token = self.reservation._portal_ensure_token()
        data = self._get(f"/api/reservations/{self.reservation.id}/precheckin/{token}")
        self.assertEqual(data["pmsPropertyPhoneNumber"], "")
        self.assertEqual(data["folioPartnerName"], "")
