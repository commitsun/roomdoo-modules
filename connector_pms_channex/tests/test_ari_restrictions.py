# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

import datetime
from unittest import mock

from odoo import fields
from odoo.tests import tagged

from odoo.addons.queue_job.tests.common import trap_jobs

from .test_rate_plans import ChannexRatePlanCase

RESTRICTIONS = "restrictions"
NEUTRAL = {
    "min_stay_arrival": 1,
    "min_stay_through": 1,
    "max_stay": 0,
    "closed_to_arrival": False,
    "closed_to_departure": False,
    "stop_sell": False,
}


class ChannexRestrictionsCase(ChannexRatePlanCase):
    """A pricelist on Channex, room only and with breakfast, that prices the
    room at 100 for its two adults, 20 less for each adult fewer, and the
    breakfast at 10 a guest. Everything is published, nothing pending."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.breakfast.per_person = True
        cls.plan = cls.env["pms.availability.plan"].create({"name": "Channex AP"})
        cls.pricelist.write(
            {"is_pms_available": True, "availability_plan_id": cls.plan.id}
        )
        cls.room_item = cls._item(cls.room_type.product_id, 100)
        cls._item(cls.breakfast, 10)
        cls.env["pms.pricelist.occupancy"].create(
            {
                "pricelist_id": cls.pricelist.id,
                "room_type_id": cls.room_type.id,
                "decrease_mode": "amount",
                "decrease_value": 20,
            }
        )

    @classmethod
    def _item(cls, product, price, pricelist=None, **values):
        """A fixed price, of a room type's product or of a board service.

        Always on the variant: pms checks the minimum price of a fixed price
        reading its variant, and fails on an item of a template.
        """
        if product._name == "product.template":
            product = product.product_variant_id
        return cls.env["product.pricelist.item"].create(
            {
                "pricelist_id": (pricelist or cls.pricelist).id,
                "applied_on": "0_product_variant",
                "product_id": product.id,
                "compute_price": "fixed",
                "fixed_price": price,
                "pms_property_ids": [(6, 0, [cls.pms_property.id])],
                **values,
            }
        )

    def setUp(self):
        super().setUp()
        # A short window: every night of it is priced in every test.
        self.env["channel.channex.pms.property"].search(
            [("backend_id", "=", self.backend.id)]
        ).state_length = 40
        self.first, self.last = self.backend._channex_ari_horizon()
        self.night = self.first + datetime.timedelta(days=10)
        self._connect()
        self._publish()
        self.server.calls.clear()
        self.room_only_plan = self._rate_plan(self.room_only)
        self.breakfast_plan = self._rate_plan(self.breakfast)

    # -- helpers -------------------------------------------------------------

    def _publish(self):
        """End the transaction, and send what it left pending."""
        with trap_jobs():
            self.env.cr.precommit.run()
        return self.backend.channex_send_ari(RESTRICTIONS)

    def _rate_plan(self, board_service, pricelist=None):
        return self._rate_plans().filtered(
            lambda r: r.board_service_id == board_service
            and r.pricelist_id == (pricelist or self.pricelist)
        )

    def _held(self, rate_plan, night):
        return self.server.restrictions.get((rate_plan.external_id, night))

    def _rates(self, rate_plan, night):
        return self._held(rate_plan, night)["rates"]

    def _restrictions(self, rate_plan, night):
        held = dict(self._held(rate_plan, night))
        held.pop("rates")
        return held

    def _sent(self):
        """The values of every send so far."""
        return [
            value
            for call in self.server.calls_to("POST", RESTRICTIONS)
            for value in call[2]["values"]
        ]

    def _days(self, days):
        return self.night + datetime.timedelta(days=days)

    def _rule(self, date_from, date_to=None, plan=None, **values):
        return self.env["pms.availability.plan.rule"].create(
            {
                "availability_plan_id": (plan or self.plan).id,
                "room_type_id": self.room_type.id,
                "pms_property_id": self.pms_property.id,
                "date_from": date_from,
                "date_to": date_to or date_from,
                **values,
            }
        )


@tagged("post_install", "-at_install")
class TestChannexRestrictionsSend(ChannexRestrictionsCase):
    """What a rate plan holds on Channex, night by night."""

    def test_a_price_for_each_number_of_adults_with_its_board_service(self):
        self.assertEqual(
            self._rates(self.room_only_plan, self.night), {1: "80.00", 2: "100.00"}
        )
        self.assertEqual(
            self._rates(self.breakfast_plan, self.night), {1: "90.00", 2: "120.00"}
        )

    def test_an_archived_variant_of_a_board_service_is_not_charged(self):
        self.env["product.product"].create(
            {"product_tmpl_id": self.breakfast.id, "active": False}
        )
        self.backend._channex_publish_everything()
        self._publish()
        self.assertEqual(
            self._rates(self.breakfast_plan, self.night), {1: "90.00", 2: "120.00"}
        )

    def test_a_night_with_no_restriction_goes_as_unrestricted(self):
        self.assertEqual(self._restrictions(self.room_only_plan, self.night), NEUTRAL)

    def test_the_whole_window_goes_in_one_call(self):
        """What Channex's certification checks: every rate plan and night in a
        single call, the nights alike in one range."""
        self.server.restrictions.clear()
        self.backend._channex_publish_everything()
        self._publish()
        [call] = self.server.calls_to("POST", RESTRICTIONS)
        values = call[2]["values"]
        self.assertEqual(len(values), 2)
        self.assertEqual(
            {(value["date_from"], value["date_to"]) for value in values},
            {(str(self.first), str(self.last))},
        )
        self.assertEqual(
            {value["rate_plan_id"] for value in values},
            {self.room_only_plan.external_id, self.breakfast_plan.external_id},
        )

    def test_a_price_changed_on_one_night_sends_that_night(self):
        self._item(
            self.room_type.product_id,
            150,
            date_start_consumption=self.night,
            date_end_consumption=self.night,
        )
        self._publish()
        self.assertEqual(len(self.server.calls_to("POST", RESTRICTIONS)), 1)
        self.assertEqual(
            {(value["date_from"], value["date_to"]) for value in self._sent()},
            {(str(self.night), str(self.night))},
        )
        self.assertEqual(self._rates(self.room_only_plan, self.night)[2], "150.00")
        self.assertEqual(self._rates(self.breakfast_plan, self.night)[2], "170.00")
        self.assertEqual(self._rates(self.room_only_plan, self._days(1))[2], "100.00")

    def test_a_board_service_price_moves_only_its_rate_plans(self):
        self._item(
            self.breakfast,
            15,
            date_start_consumption=self.night,
            date_end_consumption=self.night,
        )
        self._publish()
        self.assertEqual(
            {value["rate_plan_id"] for value in self._sent()},
            {self.breakfast_plan.external_id},
        )
        self.assertEqual(self._rates(self.breakfast_plan, self.night)[2], "130.00")

    def test_an_item_moved_publishes_the_nights_it_leaves(self):
        item = self._item(
            self.room_type.product_id,
            150,
            date_start_consumption=self.night,
            date_end_consumption=self.night,
        )
        self._publish()
        item.write(
            {
                "date_start_consumption": self._days(5),
                "date_end_consumption": self._days(5),
            }
        )
        self._publish()
        self.assertEqual(self._rates(self.room_only_plan, self.night)[2], "100.00")
        self.assertEqual(self._rates(self.room_only_plan, self._days(5))[2], "150.00")

    def test_an_item_removed_puts_the_price_back(self):
        item = self._item(
            self.room_type.product_id,
            150,
            date_start_consumption=self.night,
            date_end_consumption=self.night,
        )
        self._publish()
        item.unlink()
        self._publish()
        self.assertEqual(self._rates(self.room_only_plan, self.night)[2], "100.00")

    def test_a_pricelist_priced_from_this_one_follows_it(self):
        derived = self.env["product.pricelist"].create(
            {
                "name": "Derived PL",
                "company_id": self.company.id,
                "board_service_ids": [(6, 0, [self.room_only.id])],
                "item_ids": [
                    (
                        0,
                        0,
                        {
                            "applied_on": "3_global",
                            "compute_price": "formula",
                            "base": "pricelist",
                            "base_pricelist_id": self.pricelist.id,
                            "price_discount": 10,
                        },
                    )
                ],
            }
        )
        self._connect(derived)
        self._publish()
        derived_plan = self._rate_plan(self.room_only, derived)
        self.assertEqual(self._rates(derived_plan, self.night)[2], "90.00")
        self.server.calls.clear()
        self._item(
            self.room_type.product_id,
            200,
            date_start_consumption=self.night,
            date_end_consumption=self.night,
        )
        self._publish()
        self.assertEqual(self._rates(derived_plan, self.night)[2], "180.00")

    def test_the_price_of_a_board_service_with_no_item_follows_the_product(self):
        """With no item of the pricelist, pms prices a board service as its
        product, so the product's price is a price of the rate plans."""
        half_board = self.env["product.template"].create(
            {
                "name": "Half Board",
                "type": "service",
                "is_board_service": True,
                "per_person": True,
                "list_price": 5,
            }
        )
        self._settle(
            lambda: self.pricelist.write({"board_service_ids": [(4, half_board.id)]})
        )
        self._publish()
        rate_plan = self._rate_plan(half_board)
        self.assertEqual(self._rates(rate_plan, self.night)[2], "110.00")
        half_board.list_price = 8
        self._publish()
        self.assertEqual(self._rates(rate_plan, self.night)[2], "116.00")

    def test_an_item_of_another_property_sends_nothing(self):
        other = self.env["pms.property"].create(
            {
                "name": "Other property",
                "company_id": self.company.id,
                "default_pricelist_id": self.pricelist.id,
            }
        )
        self._item(
            self.room_type.product_id,
            150,
            date_start_consumption=self.night,
            date_end_consumption=self.night,
            pms_property_ids=[(6, 0, [other.id])],
        )
        self._publish()
        self.assertFalse(self.server.calls_to("POST", RESTRICTIONS))

    def test_a_night_at_no_price_is_closed(self):
        """Ten for the two adults leaves nothing for one: Channex takes no
        price of 0, and must not go on selling at the one it had."""
        self._item(
            self.room_type.product_id,
            10,
            date_start_consumption=self.night,
            date_end_consumption=self.night,
        )
        self._publish()
        [value] = [
            value
            for value in self._sent()
            if value["rate_plan_id"] == self.room_only_plan.external_id
        ]
        self.assertTrue(value["stop_sell"])
        self.assertNotIn("rates", value)

    def test_the_occupancy_rule_reprices_every_night(self):
        self.env["pms.pricelist.occupancy"].search(
            [("pricelist_id", "=", self.pricelist.id)]
        ).decrease_value = 30
        self._publish()
        self.assertEqual(self._rates(self.room_only_plan, self.night)[1], "70.00")
        self.assertEqual(self._rates(self.room_only_plan, self.last)[1], "70.00")

    def test_the_occupancy_a_price_is_given_for_reprices_every_night(self):
        self.room_type.default_occupancy = 1
        self._publish()
        # 100 is now the price of one adult, and nothing derives the other.
        self.assertEqual(
            self._rates(self.room_only_plan, self.night), {1: "100.00", 2: "100.00"}
        )

    def test_a_rate_plan_made_publishes_its_whole_window(self):
        half_board = self.env["product.template"].create(
            {"name": "Half Board", "type": "service", "is_board_service": True}
        )
        self._settle(
            lambda: self.pricelist.write({"board_service_ids": [(4, half_board.id)]})
        )
        self._publish()
        rate_plan = self._rate_plan(half_board)
        self.assertTrue(self._held(rate_plan, self.first))
        self.assertTrue(self._held(rate_plan, self.last))

    def test_what_channex_refuses_is_kept(self):
        self.server.store["rate_plans"] = [
            plan
            for plan in self.server.store["rate_plans"]
            if plan["id"] != self.room_only_plan.external_id
        ]
        self._item(
            self.room_type.product_id,
            150,
            date_start_consumption=self.night,
            date_end_consumption=self.night,
        )
        self._publish()
        warning = self.backend.channex_ari_warning_ids.filtered(
            lambda w: w.kind == RESTRICTIONS
        )
        self.assertEqual(warning.count, 1)
        self.assertIn(self.room_only_plan.external_id, warning.warnings)

    def test_a_large_send_is_split_in_calls_channex_takes(self):
        self._item(
            self.room_type.product_id,
            150,
            date_start_consumption=self._days(-5),
            date_end_consumption=self._days(-5),
        )
        self._item(
            self.room_type.product_id,
            160,
            date_start_consumption=self._days(5),
            date_end_consumption=self._days(5),
        )
        with mock.patch(
            "odoo.addons.connector_pms_channex.components.ari.MAX_CALL_BYTES", 1000
        ):
            self._publish()
        calls = self.server.calls_to("POST", RESTRICTIONS)
        self.assertGreater(len(calls), 1)
        self.assertEqual(self._rates(self.room_only_plan, self._days(-5))[2], "150.00")
        self.assertEqual(self._rates(self.room_only_plan, self._days(5))[2], "160.00")


@tagged("post_install", "-at_install")
class TestChannexRestrictionsRules(ChannexRestrictionsCase):
    """The restrictions of the availability plan of the pricelist."""

    def test_the_rule_of_a_night_is_its_restrictions(self):
        self._rule(
            self.night,
            min_stay=3,
            min_stay_arrival=2,
            max_stay=5,
            max_stay_arrival=4,
            closed_arrival=True,
        )
        self._publish()
        self.assertEqual(
            self._restrictions(self.room_only_plan, self.night),
            {
                **NEUTRAL,
                "min_stay_arrival": 2,
                "min_stay_through": 3,
                # The stricter of the two max stays pms has.
                "max_stay": 4,
                "closed_to_arrival": True,
            },
        )
        self.assertEqual(
            self._restrictions(self.room_only_plan, self._days(1)), NEUTRAL
        )

    def test_closed_and_closed_to_departure(self):
        self._rule(self.night, closed=True, closed_departure=True)
        self._publish()
        self.assertEqual(
            self._restrictions(self.breakfast_plan, self.night),
            {**NEUTRAL, "stop_sell": True, "closed_to_departure": True},
        )

    def test_a_rule_removed_lifts_it(self):
        rule = self._rule(self.night, closed=True)
        self._publish()
        rule.unlink()
        self._publish()
        self.assertEqual(self._restrictions(self.room_only_plan, self.night), NEUTRAL)

    def test_a_rule_moved_lifts_the_nights_it_leaves(self):
        rule = self._rule(self.night, closed=True)
        self._publish()
        rule.write({"date_from": self._days(5), "date_to": self._days(5)})
        self._publish()
        self.assertFalse(
            self._restrictions(self.room_only_plan, self.night)["stop_sell"]
        )
        self.assertTrue(
            self._restrictions(self.room_only_plan, self._days(5))["stop_sell"]
        )

    def test_the_rules_of_a_parent_plan_apply(self):
        parent = self.env["pms.availability.plan"].create({"name": "Parent AP"})
        self._rule(self.night, plan=parent, min_stay=4)
        self.plan.parent_id = parent
        self._publish()
        self.assertEqual(
            self._restrictions(self.room_only_plan, self.night)["min_stay_through"], 4
        )
        self._rule(self._days(1), plan=parent, closed=True)
        self._publish()
        self.assertTrue(
            self._restrictions(self.room_only_plan, self._days(1))["stop_sell"]
        )

    def test_a_board_service_closed_stops_selling_its_rate_plans(self):
        self.env["pms.availability.plan.board.rule"].create(
            {
                "availability_plan_id": self.plan.id,
                "pms_property_id": self.pms_property.id,
                "board_service_id": self.breakfast.id,
                "date_from": self.night,
                "date_to": self.night,
            }
        )
        self._publish()
        self.assertTrue(
            self._restrictions(self.breakfast_plan, self.night)["stop_sell"]
        )
        self.assertFalse(
            self._restrictions(self.room_only_plan, self.night)["stop_sell"]
        )

    def test_a_rule_of_another_plan_sends_nothing(self):
        other = self.env["pms.availability.plan"].create({"name": "Other AP"})
        self._rule(self.night, plan=other, closed=True)
        self._publish()
        self.assertFalse(self.server.calls_to("POST", RESTRICTIONS))

    def test_another_plan_for_the_pricelist_brings_its_restrictions(self):
        other = self.env["pms.availability.plan"].create({"name": "Other AP"})
        self._rule(self.night, plan=other, closed=True)
        self._publish()
        self.pricelist.availability_plan_id = other
        self._publish()
        self.assertTrue(
            self._restrictions(self.room_only_plan, self.night)["stop_sell"]
        )


@tagged("post_install", "-at_install")
class TestChannexRestrictionsWindow(ChannexRestrictionsCase):
    """Prices that change with nothing written: a sale window opening."""

    def test_an_item_whose_sale_window_opened_is_published(self):
        # Written while it did not apply yet, so it left nothing pending.
        self.env["product.pricelist.item"].with_context(
            connector_no_export=True
        ).create(
            {
                "pricelist_id": self.pricelist.id,
                "applied_on": "0_product_variant",
                "product_id": self.room_type.product_id.id,
                "compute_price": "fixed",
                "fixed_price": 150,
                "pms_property_ids": [(6, 0, [self.pms_property.id])],
                "date_start_consumption": self.night,
                "date_end_consumption": self.night,
                "date_start": fields.Datetime.now() - datetime.timedelta(hours=1),
            }
        )
        self.backend.channex_cron_ari_horizon()
        self._publish()
        self.assertEqual(self._rates(self.room_only_plan, self.night)[2], "150.00")
