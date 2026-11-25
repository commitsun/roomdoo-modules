# Copyright 2026 Commit [Sun]
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
"""The API gives what a client needs to price a room type by its occupancy.

Room types tell the occupancy their price is given for and the one their rooms
guarantee, rooms tell their children places, and the prices endpoint prices a
room type for the guests staying in it, as the nights of a reservation are.
"""
import datetime

from odoo.tests import tagged

from odoo.addons.base_rest.controllers.main import _PseudoCollection
from odoo.addons.component.core import WorkContext
from odoo.addons.pms.tests.common import TestPms


@tagged("post_install", "-at_install")
class TestRoomTypeOccupancy(TestPms):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Admin (uid=1) avoids record rules; the endpoints sudo() anyway.
        cls.env = cls.env(user=cls.env["res.users"].browse(1))
        cls.env.user.write(
            {
                "company_ids": [(4, cls.pms_property1.company_id.id)],
                "pms_property_ids": [(4, cls.pms_property1.id)],
            }
        )
        cls.room_type = cls.env["pms.room.type"].create(
            {
                "pms_property_ids": [cls.pms_property1.id],
                "name": "Triple Occupancy",
                "default_code": "TRP_OCC",
                "class_id": cls.room_type_class1.id,
            }
        )
        cls.rooms = cls.env["pms.room"].create(
            [
                {
                    "pms_property_id": cls.pms_property1.id,
                    "name": "Room %s" % number,
                    "short_name": "O%s" % number,
                    "room_type_id": cls.room_type.id,
                    "capacity": 3,
                    "children_capacity": 1,
                }
                for number in (501, 502)
            ]
        )
        # Priced for two adults, holding three
        cls.room_type.default_occupancy = 2
        cls.date = datetime.date.today() + datetime.timedelta(days=30)
        cls.env["product.pricelist.item"].create(
            {
                "pricelist_id": cls.pricelist1.id,
                "applied_on": "0_product_variant",
                "product_id": cls.room_type.product_id.id,
                "compute_price": "fixed",
                "fixed_price": 100.0,
                "date_start_consumption": cls.date,
                "date_end_consumption": cls.date,
                "pms_property_ids": [(6, 0, [cls.pms_property1.id])],
            }
        )
        cls.env["pms.pricelist.occupancy"].create(
            {
                "pricelist_id": cls.pricelist1.id,
                "room_type_id": cls.room_type.id,
                "increase_mode": "amount",
                "increase_value": 30.0,
                "decrease_mode": "percent",
                "decrease_value": 25.0,
            }
        )

    def _service(self, usage):
        collection = _PseudoCollection("pms.services", self.env)
        work = WorkContext(
            model_name="rest.service.registration", collection=collection
        )
        return work.component(usage=usage)

    def _room_price(self, **guests):
        param = self.env.datamodels["pms.price.search.param"](
            pmsPropertyId=self.pms_property1.id,
            pricelistId=self.pricelist1.id,
            roomTypeId=self.room_type.id,
            dateFrom=self.date.isoformat(),
            dateTo=self.date.isoformat(),
            **guests,
        )
        (price,) = self._service("prices").get_prices(param)
        return price.price

    def test_room_type_tells_its_occupancies(self):
        param = self.env.datamodels["pms.room.type.search.param"](
            pmsPropertyIds=[self.pms_property1.id]
        )
        (room_type,) = (
            info
            for info in self._service("room-types").get_room_types(param)
            if info.id == self.room_type.id
        )
        self.assertEqual(room_type.maxOccupancy, 3)
        self.assertEqual(room_type.defaultOccupancy, 2)
        room_type = self._service("room-types").get_restricted_room_type(
            self.room_type.id
        )
        self.assertEqual(room_type.maxOccupancy, 3)
        self.assertEqual(room_type.defaultOccupancy, 2)

    def test_room_tells_its_children_places(self):
        room = self._service("rooms").get_room(self.rooms[0].id)
        self.assertEqual(room.capacity, 3)
        self.assertEqual(room.childrenCapacity, 1)

    def test_price_without_guests_is_the_default_occupancy_one(self):
        self.assertEqual(self._room_price(), 100.0)
        self.assertEqual(self._room_price(adults=2), 100.0)

    def test_price_follows_the_adults_staying(self):
        self.assertEqual(self._room_price(adults=3), 130.0)
        self.assertEqual(self._room_price(adults=1), 75.0)

    def test_children_alone_leave_the_room_price_as_is(self):
        # pms charges no fee per child: modules adding one get the children
        self.assertEqual(self._room_price(adults=2, children=1), 100.0)
