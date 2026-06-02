from datetime import date, timedelta

from odoo.addons.pms.tests.common import TestPms


class TestBilledDayPrice(TestPms):
    """Unit tests for pms.board.service.room.type._get_billed_day_price.

    The helper must mirror the pricing path of
    ``pms.service.line._get_price_unit_line`` so external-API callers
    (POST /folios from OTAs, Wubook connector) can split a package
    price (room + board) into the room portion without diverging from
    the price the auto-computed service line will ultimately persist.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.room_type_class1.pms_property_ids = [(6, 0, [cls.pms_property1.id])]
        cls.room_type1 = cls.env["pms.room.type"].create(
            {
                "name": "Room Type 1",
                "default_code": "RT1",
                "class_id": cls.room_type_class1.id,
                "pms_property_ids": [(6, 0, [cls.pms_property1.id])],
                "list_price": 50.0,
            }
        )
        cls.breakfast = cls.env["product.product"].create(
            {
                "name": "Breakfast",
                "list_price": 8.0,
                "per_person": True,
            }
        )
        cls.board = cls.env["pms.board.service"].create(
            {
                "name": "Board BB",
                "default_code": "BB",
                "pms_property_ids": [(6, 0, [cls.pms_property1.id])],
                "board_service_line_ids": [
                    (
                        0,
                        False,
                        {
                            "product_id": cls.breakfast.id,
                            "amount": 8.0,
                            "adults": True,
                        },
                    ),
                ],
            }
        )
        cls.bsrt = cls.env["pms.board.service.room.type"].create(
            {
                "pms_board_service_id": cls.board.id,
                "pms_room_type_id": cls.room_type1.id,
                "pms_property_id": cls.pms_property1.id,
                "board_service_line_ids": [
                    (
                        0,
                        False,
                        {
                            "product_id": cls.breakfast.id,
                            "amount": 5.5,
                            "adults": True,
                        },
                    ),
                ],
            }
        )

    def test_no_adults_no_children_returns_zero(self):
        self.assertEqual(
            self.bsrt._get_billed_day_price(
                pricelist=self.pricelist1,
                consumption_date=date.today() + timedelta(days=10),
                pms_property_id=self.pms_property1.id,
                adults=0,
                children=0,
            ),
            0.0,
        )

    def test_adults_uses_bsrt_line_amount(self):
        """Without fiscal position remapping, the helper routes through
        the pricelist path and returns ``bsrt_line.amount × adults``.
        The previous buggy code computed this directly from ``amount``
        without going through the pricelist, missing property- and
        pricelist-specific overrides — this test pins the new behavior
        so future regressions surface immediately."""
        self.assertEqual(
            self.bsrt._get_billed_day_price(
                pricelist=self.pricelist1,
                consumption_date=date.today() + timedelta(days=10),
                pms_property_id=self.pms_property1.id,
                adults=2,
                children=0,
            ),
            11.0,
        )

    def test_skips_children_line_when_children_zero(self):
        """A children-only line must not contribute when ``children=0``,
        even if the line ``amount`` is non-zero."""
        self.env["pms.board.service.room.type.line"].create(
            {
                "pms_board_service_room_type_id": self.bsrt.id,
                "product_id": self.breakfast.id,
                "amount": 4.0,
                "children": True,
            }
        )
        self.assertEqual(
            self.bsrt._get_billed_day_price(
                pricelist=self.pricelist1,
                consumption_date=date.today() + timedelta(days=10),
                pms_property_id=self.pms_property1.id,
                adults=2,
                children=0,
            ),
            11.0,
        )
