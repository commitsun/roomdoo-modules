from datetime import date, timedelta

from odoo.addons.pms.tests.common import TestPms


class TestBilledDayPrice(TestPms):
    """Unit tests for product.template._get_billed_day_price.

    The helper must mirror the pricing path of
    ``pms.service._get_price_unit_line`` so external-API callers
    (POST /folios from OTAs) can split a package price (room + board)
    into the room portion without diverging from the price the board
    service lines will ultimately persist.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.board = cls._create_board_service(
            "Board BB",
            guests=("adult", "child"),
            default_code="BB",
            list_price=8.0,
            per_person=True,
            pms_property_ids=[(6, 0, [cls.pms_property1.id])],
        )
        cls.adult_breakfast = cls._board_service_variant(cls.board, "adults")
        cls.child_breakfast = cls._board_service_variant(cls.board, "children")
        for product, price in ((cls.adult_breakfast, 5.5), (cls.child_breakfast, 3)):
            cls.env["product.pricelist.item"].create(
                {
                    "pricelist_id": cls.pricelist1.id,
                    "applied_on": "0_product_variant",
                    "product_id": product.id,
                    "compute_price": "fixed",
                    "fixed_price": price,
                }
            )
        cls.day = date.today() + timedelta(days=10)

    def _billed_day_price(self, adults, children):
        return self.board._get_billed_day_price(
            pricelist=self.pricelist1,
            consumption_date=self.day,
            pms_property_id=self.pms_property1.id,
            adults=adults,
            children=children,
        )

    def test_no_adults_no_children_returns_zero(self):
        self.assertEqual(self._billed_day_price(0, 0), 0.0)

    def test_adults_use_the_pricelist_price(self):
        """The price comes from the pricelist, not from the sale price of the
        board service, and is counted once for each adult."""
        self.assertEqual(self._billed_day_price(2, 0), 11.0)

    def test_children_variant_skipped_without_children(self):
        """The variant for children doesn't contribute when ``children=0``,
        even if its price is non-zero."""
        self.assertEqual(self._billed_day_price(2, 0), 11.0)

    def test_children_use_their_own_price(self):
        self.assertEqual(self._billed_day_price(2, 1), 14.0)
