# Copyright 2026 Commit [Sun]
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
"""``get_availability_plan_rules`` builds its query with raw SQL and
interpolates the target dates and the room types of the property as SQL
tuples. An empty tuple renders as ``IN ()``, which Postgres rejects with a
syntax error, so the endpoint answered 500 instead of an empty list in two
reachable situations:

* a property with no rooms configured (no room types to look rules up for),
* a reversed date range, which produces no target dates at all.

Both mean "there are no rules" and must return an empty list.
"""
from datetime import date, timedelta

from odoo.tests import tagged

from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.addons.base_rest.controllers.main import _PseudoCollection
from odoo.addons.component.core import WorkContext
from odoo.addons.pms.tests.common import TestPms


@tagged("post_install", "-at_install")
class TestAvailabilityPlanService(TestPms, AccountTestInvoicingCommon):
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
                "date": today,
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
