# Copyright 2026 Commit [Sun]
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
"""The restrictions are stored by date range and the API talks by night.

Two degenerate inputs mean "there are no rules" and must answer with an
empty list rather than an error: a property with no rooms configured, and a
reversed date range. The rest covers the translation itself, in both
directions.
"""
from datetime import date, timedelta

from odoo.tests import tagged

from odoo.addons.base_rest.controllers.main import _PseudoCollection
from odoo.addons.component.core import WorkContext
from odoo.addons.pms.tests.common import TestPms


@tagged("post_install", "-at_install")
class TestAvailabilityPlanService(TestPms):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # The endpoint sudo()es its own lookups; use admin so record rules do
        # not interfere with the access checks.
        cls.env = cls.env(user=cls.env["res.users"].browse(1))
        # pms_api_check_access() requires the caller to be assigned to the
        # property whose rooms are being read.
        cls.pms_property1.user_ids = [(4, cls.env.user.id)]

    def _service(self):
        collection = _PseudoCollection("pms.services", self.env)
        work = WorkContext(
            model_name="rest.service.registration", collection=collection
        )
        return work.component(usage="availability-plans")

    def _search_param(self, date_from, date_to, pms_property=None):
        return self.env.datamodels["pms.availability.plan.rule.search.param"](
            dateFrom=str(date_from),
            dateTo=str(date_to),
            pmsPropertyId=(pms_property or self.pms_property1).id,
        )

    def _add_room(self):
        room_type = self.env["pms.room.type"].create(
            {
                "pms_property_ids": [self.pms_property1.id],
                "name": "Double Avail Test",
                "default_code": "DBL_Avail",
                "class_id": self.room_type_class1.id,
                "list_price": 25,
            }
        )
        self.env["pms.room"].create(
            {
                "pms_property_id": self.pms_property1.id,
                "name": "Double 201",
                "room_type_id": room_type.id,
                "capacity": 2,
            }
        )
        return room_type

    def test_no_rooms_returns_empty_list(self):
        """A property with no rooms has no room types, hence no rules."""
        rooms = self.env["pms.room"].search(
            [("pms_property_id", "=", self.pms_property1.id)]
        )
        self.assertFalse(rooms)
        today = date.today()
        result = self._service().get_availability_plan_rules(
            self.availability_plan1.id,
            self._search_param(today, today + timedelta(days=2)),
        )
        self.assertEqual(result, [])

    def test_reversed_date_range_returns_empty_list(self):
        """dateTo before dateFrom yields no target dates."""
        self._add_room()
        today = date.today()
        result = self._service().get_availability_plan_rules(
            self.availability_plan1.id,
            self._search_param(today, today - timedelta(days=1)),
        )
        self.assertEqual(result, [])

    def test_existing_rule_is_returned(self):
        """The early return must not shadow the regular path."""
        room_type = self._add_room()
        today = date.today()
        self.env["pms.availability.plan.rule"].create(
            {
                "availability_plan_id": self.availability_plan1.id,
                "pms_property_id": self.pms_property1.id,
                "room_type_id": room_type.id,
                "date_from": today,
                "date_to": today,
                "min_stay": 2,
            }
        )
        result = self._service().get_availability_plan_rules(
            self.availability_plan1.id,
            self._search_param(today, today),
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].roomTypeId, room_type.id)
        self.assertEqual(result[0].minStay, 2)

    def test_a_range_is_returned_night_by_night(self):
        """One record covers a period; the payload is still per night."""
        room_type = self._add_room()
        today = date.today()
        self.env["pms.availability.plan.rule"].create(
            {
                "availability_plan_id": self.availability_plan1.id,
                "pms_property_id": self.pms_property1.id,
                "room_type_id": room_type.id,
                "date_from": today,
                "date_to": today + timedelta(days=2),
                "min_stay": 3,
            }
        )
        result = self._service().get_availability_plan_rules(
            self.availability_plan1.id,
            self._search_param(today, today + timedelta(days=2)),
        )
        self.assertEqual(len(result), 3)
        self.assertEqual({rule.minStay for rule in result}, {3})

    def test_overlapping_ranges_return_the_last_written_one(self):
        room_type = self._add_room()
        today = date.today()
        Rule = self.env["pms.availability.plan.rule"]
        base = {
            "availability_plan_id": self.availability_plan1.id,
            "pms_property_id": self.pms_property1.id,
            "room_type_id": room_type.id,
        }
        Rule.create(
            {
                **base,
                "date_from": today,
                "date_to": today + timedelta(days=4),
                "min_stay": 7,
            }
        )
        Rule.create(
            {
                **base,
                "date_from": today + timedelta(days=1),
                "date_to": today + timedelta(days=2),
                "min_stay": 2,
            }
        )
        result = self._service().get_availability_plan_rules(
            self.availability_plan1.id,
            self._search_param(today, today + timedelta(days=4)),
        )
        by_date = {rule.date[:10]: rule.minStay for rule in result}
        self.assertEqual(by_date[str(today)], 7)
        self.assertEqual(by_date[str(today + timedelta(days=1))], 2)
        self.assertEqual(by_date[str(today + timedelta(days=2))], 2)
        self.assertEqual(by_date[str(today + timedelta(days=3))], 7)

    def _rules_payload(self, room_type, dates, **values):
        RuleInfo = self.env.datamodels["pms.availability.plan.rule.info"]
        return self.env.datamodels["pms.availability.plan.rules.info"](
            availabilityPlanRules=[
                RuleInfo(
                    availabilityPlanId=self.availability_plan1.id,
                    pmsPropertyId=self.pms_property1.id,
                    roomTypeId=room_type.id,
                    date=str(day),
                    **values,
                )
                for day in dates
            ]
        )

    def test_consecutive_nights_are_written_as_one_range(self):
        """The front sends a night at a time; what is stored is a period."""
        room_type = self._add_room()
        today = date.today()
        dates = [today + timedelta(days=i) for i in range(3)]
        self._service().update_availability_plan_rules(
            self._rules_payload(room_type, dates, minStay=4)
        )
        rules = self.env["pms.availability.plan.rule"].search(
            [
                ("availability_plan_id", "=", self.availability_plan1.id),
                ("room_type_id", "=", room_type.id),
            ]
        )
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules.date_from, today)
        self.assertEqual(rules.date_to, today + timedelta(days=2))
        self.assertEqual(rules.min_stay, 4)

    def test_a_partial_change_keeps_what_already_applied(self):
        """The payload only carries the fields being changed, so the rest has
        to come from the rule already covering the night."""
        room_type = self._add_room()
        today = date.today()
        self.env["pms.availability.plan.rule"].create(
            {
                "availability_plan_id": self.availability_plan1.id,
                "pms_property_id": self.pms_property1.id,
                "room_type_id": room_type.id,
                "date_from": today,
                "date_to": today + timedelta(days=2),
                "min_stay": 5,
            }
        )
        self._service().update_availability_plan_rules(
            self._rules_payload(room_type, [today + timedelta(days=1)], closed=True)
        )
        result = self._service().get_availability_plan_rules(
            self.availability_plan1.id,
            self._search_param(today, today + timedelta(days=2)),
        )
        by_date = {rule.date[:10]: rule for rule in result}
        changed = by_date[str(today + timedelta(days=1))]
        self.assertTrue(changed.closed)
        self.assertEqual(changed.minStay, 5)
        self.assertFalse(by_date[str(today)].closed)
