# Copyright 2024 Commit [Sun]
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import datetime

from odoo.addons.pms.tests.common import TestPms


class TestTelevesConnector(TestPms):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        # Enable Televes integration on the test property
        cls.pms_property1.write(
            {
                "televes_enabled": True,
                "televes_url": "http://televes-test.example.com:8094",
                "televes_base_path": "/pms-adapter-backend-service",
                "televes_pms_user": "userTest",
                "televes_pms_password": "testpassword",
            }
        )

        # Room type
        cls.room_type1 = cls.env["pms.room.type"].create(
            {
                "pms_property_ids": [cls.pms_property1.id],
                "name": "Double Televes Test",
                "default_code": "DBL_TV_TEST",
                "class_id": cls.room_type_class1.id,
                "list_price": 100,
            }
        )

        # Room 1 with Televes room number
        cls.room1 = cls.env["pms.room"].create(
            {
                "pms_property_id": cls.pms_property1.id,
                "name": "Room 101",
                "room_type_id": cls.room_type1.id,
                "capacity": 2,
                "televes_room_number": 5000,
            }
        )

        # Room 2 for changeroom tests
        cls.room2 = cls.env["pms.room"].create(
            {
                "pms_property_id": cls.pms_property1.id,
                "name": "Room 102",
                "room_type_id": cls.room_type1.id,
                "capacity": 2,
                "televes_room_number": 5001,
            }
        )

        # Partner
        cls.partner1 = cls.env["res.partner"].create(
            {
                "name": "Test Guest",
            }
        )

        # Sale channel
        cls.sale_channel1 = cls.env["pms.sale.channel"].create(
            {
                "name": "Direct Televes Test",
                "channel_type": "direct",
            }
        )

    def _create_reservation(self, checkin=None, checkout=None):
        """Helper: create a basic reservation for televes tests."""
        if checkin is None:
            checkin = datetime.date.today()
        if checkout is None:
            checkout = datetime.date.today() + datetime.timedelta(days=1)
        return self.env["pms.reservation"].create(
            {
                "pms_property_id": self.pms_property1.id,
                "checkin": checkin,
                "checkout": checkout,
                "adults": 1,
                "preferred_room_id": self.room1.id,
                "partner_id": self.partner1.id,
                "sale_channel_origin_id": self.sale_channel1.id,
            }
        )

    def _create_checkin_partner(self, reservation):
        """Helper: create or retrieve the first checkin partner for the reservation."""
        if reservation.checkin_partner_ids:
            return reservation.checkin_partner_ids.sorted("id")[0]
        return self.env["pms.checkin.partner"].create(
            {
                "reservation_id": reservation.id,
                "partner_id": self.partner1.id,
            }
        )
