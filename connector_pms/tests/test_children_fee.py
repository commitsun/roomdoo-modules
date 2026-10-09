# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
import datetime

from odoo.addons.pms.tests.common import TestPms


class TestChildrenFee(TestPms):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.consumption_date = datetime.date(2012, 1, 14)
        cls.room_type = cls.env["pms.room.type"].create(
            {
                "pms_property_ids": [cls.pms_property1.id],
                "name": "Family",
                "default_code": "FAM",
                "class_id": cls.room_type_class1.id,
            }
        )
        cls.env["pms.room"].create(
            {
                "pms_property_id": cls.pms_property1.id,
                "name": "Family-301",
                "room_type_id": cls.room_type.id,
                "capacity": 4,
                "children_capacity": 2,
            }
        )
        cls.env["product.pricelist.item"].create(
            {
                "pricelist_id": cls.pricelist1.id,
                "applied_on": "0_product_variant",
                "product_id": cls.room_type.product_id.id,
                "compute_price": "fixed",
                "fixed_price": 100.0,
                "date_start_consumption": cls.consumption_date,
                "date_end_consumption": cls.consumption_date,
                "pms_property_ids": [(6, 0, [cls.pms_property1.id])],
            }
        )
        cls.partner1 = cls.env["res.partner"].create({"name": "Partner 1"})
        cls.sale_channel_direct = cls.env["pms.sale.channel"].create(
            {"name": "Door", "channel_type": "direct"}
        )
        cls.occupancy = cls.env["pms.pricelist.occupancy"].create(
            {
                "pricelist_id": cls.pricelist1.id,
                "room_type_id": cls.room_type.id,
            }
        )

    def _price(self, adults, children=0):
        return self.pricelist1._get_product_price(
            product=self.room_type.product_id,
            quantity=1,
            consumption_date=self.consumption_date,
            pms_property_id=self.pms_property1.id,
            occupancy=adults,
            children=children,
        )

    def test_children_are_free_without_a_fee(self):
        """A pricelist with no children fee charges the room alone."""
        self.assertAlmostEqual(self._price(4, children=2), 100.0, places=2)

    def test_children_fee_is_charged_per_child(self):
        """Each child adds the fee to the price of the night."""
        self.occupancy.children_fee = 15.0
        self.assertAlmostEqual(self._price(4, children=1), 115.0, places=2)
        self.assertAlmostEqual(self._price(4, children=2), 130.0, places=2)

    def test_children_fee_applies_over_the_occupancy_price(self):
        """The fee is added on top of the price derived for the adults.

        The occupancy of a room type counts adults alone, so the price is
        derived for them first and the children are charged over that.
        """
        self.occupancy.write(
            {
                "decrease_mode": "percent",
                "decrease_value": 25.0,
                "children_fee": 15.0,
            }
        )
        # 3 adults is one below the default occupancy of 4: 100 - 25% = 75
        self.assertAlmostEqual(self._price(3), 75.0, places=2)
        self.assertAlmostEqual(self._price(3, children=1), 90.0, places=2)

    def test_changing_the_children_reprices_the_reservation(self):
        """Children are part of the price, so changing how many reprices."""
        # ARRANGE
        self.occupancy.children_fee = 15.0
        reservation = self.env["pms.reservation"].create(
            {
                "pms_property_id": self.pms_property1.id,
                "room_type_id": self.room_type.id,
                "pricelist_id": self.pricelist1.id,
                "partner_id": self.partner1.id,
                "sale_channel_origin_id": self.sale_channel_direct.id,
                "checkin": self.consumption_date,
                "checkout": self.consumption_date + datetime.timedelta(days=1),
                "adults": 4,
            }
        )
        price_without_children = reservation.reservation_line_ids[0].price
        self.assertTrue(price_without_children, "The night needs a price")
        # ACT
        reservation.children = 1
        # ASSERT
        self.assertAlmostEqual(
            reservation.reservation_line_ids[0].price,
            price_without_children + 15.0,
            places=2,
            msg="Adding a child should add its fee to the night",
        )

    def test_price_without_children_is_untouched(self):
        """A room type priced without children keeps the plain price."""
        self.occupancy.children_fee = 15.0
        self.assertAlmostEqual(self._price(4), 100.0, places=2)
