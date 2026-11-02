# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

import datetime

from odoo import fields
from odoo.exceptions import ValidationError
from odoo.tests import tagged

from odoo.addons.queue_job.tests.common import trap_jobs

from .test_ari_restrictions import ChannexRestrictionsCase


class ChannexSoldLimitsCase(ChannexRestrictionsCase):
    """The pricelist on Channex, and another one and a plan that are not."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        guest = cls.env.ref("pms.product_attribute_board_guest")
        cls.half_board = cls.env["product.template"].create(
            {
                "name": "Half board",
                "type": "service",
                "is_board_service": True,
                "per_person": True,
                "attribute_line_ids": [
                    (
                        0,
                        0,
                        {
                            "attribute_id": guest.id,
                            "value_ids": [(6, 0, guest.value_ids.ids)],
                        },
                    )
                ],
            }
        )
        variants = cls.half_board.product_variant_ids
        cls.adult_meal = variants.filtered(lambda p: p.board_guest_type == "adults")
        cls.child_meal = variants.filtered(lambda p: p.board_guest_type == "children")
        cls.other_plan = cls.env["pms.availability.plan"].create({"name": "Other AP"})
        cls.other_pricelist = cls.env["product.pricelist"].create(
            {
                "name": "Other PL",
                "company_id": cls.company.id,
                "is_pms_available": True,
                "availability_plan_id": cls.other_plan.id,
                "board_service_ids": [(6, 0, [cls.room_only.id, cls.half_board.id])],
            }
        )

    def setUp(self):
        super().setUp()
        self.today = fields.Date.context_today(self.pricelist)

    def _dated(self, product, price, pricelist=None):
        return self._item(
            product,
            price,
            pricelist=pricelist,
            date_start_consumption=self.night,
            date_end_consumption=self._days(5),
        )

    def _sell_half_board(self):
        self.pricelist.board_service_ids |= self.half_board


@tagged("post_install", "-at_install")
class TestChannexChildrenBoard(ChannexSoldLimitsCase):
    """What a child consumes of a board service has one price, the same every
    night, in a pricelist sold through Channex."""

    def test_a_board_service_for_everybody_cannot_be_priced_by_date(self):
        """Per person and with no variant for children, children pay what
        adults pay."""
        with self.assertRaises(ValidationError) as error:
            self._dated(self.breakfast, 12)
        message = str(error.exception)
        self.assertIn("Channex PL", message)
        self.assertIn("Breakfast", message)
        self.assertIn(self.env.ref("pms.product_attribute_board_guest").name, message)

    def test_the_adults_of_a_board_service_with_variants_can(self):
        self._sell_half_board()
        self._item(self.child_meal, 6)
        self._dated(self.adult_meal, 18)

    def test_its_children_cannot(self):
        self._sell_half_board()
        with self.assertRaises(ValidationError) as error:
            self._dated(self.child_meal, 8)
        self.assertIn(self.child_meal.display_name, str(error.exception))

    def test_a_board_service_charged_per_room_can(self):
        """Children add nothing to it."""
        self.breakfast.per_person = False
        self._dated(self.breakfast, 12)

    def test_charging_it_per_person_then_is_refused(self):
        self.breakfast.per_person = False
        self._dated(self.breakfast, 12)
        with self.assertRaises(ValidationError):
            self.breakfast.per_person = True

    def test_a_dated_price_for_the_whole_pricelist_reaches_children(self):
        """Those with no price of their own."""
        self._sell_half_board()
        values = {
            "pricelist_id": self.pricelist.id,
            "applied_on": "3_global",
            "compute_price": "percentage",
            "percent_price": 10,
            "date_start_consumption": self.night,
        }
        with self.assertRaises(ValidationError):
            self.env["product.pricelist.item"].create(values)
        self._item(self.child_meal, 6)
        self.env["product.pricelist.item"].create(values)

    def test_removing_a_child_s_own_price_hands_it_to_a_dated_one(self):
        self._sell_half_board()
        own = self._item(self.child_meal, 6)
        self.env["product.pricelist.item"].create(
            {
                "pricelist_id": self.pricelist.id,
                "applied_on": "3_global",
                "compute_price": "percentage",
                "percent_price": 10,
                "date_start_consumption": self.night,
            }
        )
        with self.assertRaises(ValidationError):
            own.unlink()

    def test_nor_by_sale_dates(self):
        self._sell_half_board()
        with self.assertRaises(ValidationError):
            self._item(
                self.child_meal, 6, date_start=fields.Datetime.now(), date_end=False
            )

    def test_a_price_taken_from_another_pricelist_that_has_dates(self):
        self._sell_half_board()
        self._dated(self.child_meal, 8, pricelist=self.other_pricelist)
        with self.assertRaises(ValidationError) as error:
            self._item(
                self.child_meal,
                0,
                compute_price="formula",
                base="pricelist",
                base_pricelist_id=self.other_pricelist.id,
            )
        self.assertIn("Other PL", str(error.exception))

    def test_dating_it_in_the_other_pricelist_is_refused_too(self):
        self._sell_half_board()
        self._item(
            self.child_meal,
            0,
            compute_price="formula",
            base="pricelist",
            base_pricelist_id=self.other_pricelist.id,
        )
        with self.assertRaises(ValidationError):
            self._dated(self.child_meal, 8, pricelist=self.other_pricelist)

    def test_a_price_that_is_over_does_not_count(self):
        """Old seasons pile up, and price nothing any more."""
        self._sell_half_board()
        yesterday = self.today - datetime.timedelta(days=1)
        self._item(
            self.child_meal,
            8,
            date_start_consumption=yesterday - datetime.timedelta(days=5),
            date_end_consumption=yesterday,
        )
        self._item(
            self.child_meal,
            8,
            date_end=fields.Datetime.now() - datetime.timedelta(days=1),
        )

    def test_a_pricelist_not_sold_through_channex_can(self):
        self._dated(self.child_meal, 8, pricelist=self.other_pricelist)

    def test_selling_a_board_service_with_a_dated_child_price_is_refused(self):
        self._dated(self.child_meal, 8)
        with self.assertRaises(ValidationError):
            self._sell_half_board()

    def test_connecting_a_pricelist_with_one_is_refused(self):
        self._dated(self.child_meal, 8, pricelist=self.other_pricelist)
        with self.assertRaises(ValidationError):
            self._connect(self.other_pricelist)

    def test_choosing_it_on_the_backend_is_refused(self):
        self._dated(self.child_meal, 8, pricelist=self.other_pricelist)
        with self.assertRaises(ValidationError), trap_jobs():
            self.backend.channex_pricelist_ids |= self.other_pricelist


@tagged("post_install", "-at_install")
class TestChannexChildrenFee(ChannexSoldLimitsCase):
    """What a child adds to the board service is in the children fee."""

    def test_the_children_fee_includes_the_board_service_a_child_adds(self):
        def sell():
            self.env["pms.pricelist.occupancy"].search(
                [("pricelist_id", "=", self.pricelist.id)]
            ).children_fee = 5
            self._dated(self.adult_meal, 18)
            self._item(self.child_meal, 6)
            self._sell_half_board()

        self._settle(sell)
        self.assertEqual(self._plan("Channex PL – Room Only")["children_fee"], "5.00")
        # Per person and for everybody: a child pays what an adult does.
        self.assertEqual(self._plan("Channex PL – Breakfast")["children_fee"], "15.00")
        self.assertEqual(self._plan("Channex PL – Half board")["children_fee"], "11.00")

    def test_a_new_price_for_children_reaches_the_rate_plans(self):
        item = self._item(self.child_meal, 6)
        self._settle(self._sell_half_board)
        self.assertEqual(self._plan("Channex PL – Half board")["children_fee"], "6.00")
        self._settle(lambda: setattr(item, "fixed_price", 7))
        self.assertEqual(self._plan("Channex PL – Half board")["children_fee"], "7.00")

    def test_and_so_does_charging_children_for_the_board_service(self):
        self._settle(lambda: setattr(self.breakfast, "per_person", False))
        self.assertEqual(self._plan("Channex PL – Breakfast")["children_fee"], "0.00")
        self._settle(lambda: setattr(self.breakfast, "per_person", True))
        self.assertEqual(self._plan("Channex PL – Breakfast")["children_fee"], "10.00")


@tagged("post_install", "-at_install")
class TestChannexMaxStayArrival(ChannexSoldLimitsCase):
    """Channex has a single max stay, so a plan sold there has no max stay on
    arrival."""

    def test_it_is_refused_under_a_pricelist_sold_through_channex(self):
        with self.assertRaises(ValidationError) as error:
            self._rule(self.night, max_stay_arrival=3)
        message = str(error.exception)
        self.assertIn("Channex AP", message)
        self.assertIn("Channex PL", message)

    def test_and_in_a_plan_it_inherits_from(self):
        self.plan.parent_id = self.other_plan
        with self.assertRaises(ValidationError):
            self._rule(self.night, plan=self.other_plan, max_stay_arrival=3)

    def test_a_plan_not_sold_through_channex_can(self):
        self._rule(self.night, plan=self.other_plan, max_stay_arrival=3)

    def test_so_can_a_rule_that_is_over(self):
        """It is not published again."""
        yesterday = self.today - datetime.timedelta(days=1)
        self._rule(
            yesterday - datetime.timedelta(days=5), yesterday, max_stay_arrival=3
        )

    def test_setting_it_on_a_rule_is_refused(self):
        rule = self._rule(self.night, max_stay=5)
        with self.assertRaises(ValidationError):
            rule.max_stay_arrival = 3

    def test_connecting_a_pricelist_whose_plan_has_one_is_refused(self):
        self._rule(self.night, plan=self.other_plan, max_stay_arrival=3)
        with self.assertRaises(ValidationError) as error:
            self._connect(self.other_pricelist)
        message = str(error.exception)
        self.assertIn("Other PL", message)
        self.assertIn("Other AP", message)

    def test_one_that_is_over_does_not_stop_connecting_it(self):
        yesterday = self.today - datetime.timedelta(days=1)
        self._rule(
            yesterday - datetime.timedelta(days=5),
            yesterday,
            plan=self.other_plan,
            max_stay_arrival=3,
        )
        self._connect(self.other_pricelist)

    def test_selling_a_pricelist_under_such_a_plan_is_refused(self):
        self._rule(self.night, plan=self.other_plan, max_stay_arrival=3)
        with self.assertRaises(ValidationError):
            self.pricelist.availability_plan_id = self.other_plan

    def test_inheriting_from_such_a_plan_is_refused(self):
        self._rule(self.night, plan=self.other_plan, max_stay_arrival=3)
        with self.assertRaises(ValidationError):
            self.plan.parent_id = self.other_plan
