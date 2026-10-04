# Copyright 2026 Commit [Sun]
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
"""The commercial inventory left ``pms.availability.plan.rule`` and lives in
``pms.inventory.rule``, but the SPA still sends ``quota`` and
``maxAvailability`` on the plan rule payload of the calendar. The bridge in
``pms.availability.plan.service`` translates that, and the contract the front
has always seen has to survive the translation.

The case worth pinning down is a payload that carries only ONE of the two
values, which is what happens when a user has only one of them configured in
the calendar. An inventory rule at the general scope REPLACES the room type
defaults in both fields, so the field left out cannot be left to the -1 the
column defaults to: that would put on sale a room type its own default keeps
closed. The plan rules filled it with the room type default through a stored
compute, and that is what is reproduced here.

The other half of the translation is the shape: the payload carries one item
per night, and the model expresses a period as one record, so the nights that
share a room type and the same values have to end up in one rule.
"""
import datetime

from odoo import fields
from odoo.tests import tagged

from odoo.addons.base_rest.controllers.main import _PseudoCollection
from odoo.addons.component.core import WorkContext
from odoo.addons.pms.tests.common import TestPms


@tagged("post_install", "-at_install")
class TestAvailabilityPlanInventoryBridge(TestPms):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.today = fields.Date.today()
        cls.pricelist_bridge = cls.env["product.pricelist"].create(
            {
                "name": "Bridge pricelist",
                "availability_plan_id": cls.availability_plan1.id,
                "is_pms_available": True,
            }
        )
        cls.property_bridge = cls.env["pms.property"].create(
            {
                "name": "Bridge property",
                "company_id": cls.company1.id,
                "default_pricelist_id": cls.pricelist_bridge.id,
            }
        )
        # The shape most of the production data has: a quota configured and a
        # max availability closed by default, so lifting it is visible.
        cls.room_type_bridge = cls.env["pms.room.type"].create(
            {
                "pms_property_ids": [(4, cls.property_bridge.id)],
                "name": "Bridge room type",
                "default_code": "BRDG",
                "class_id": cls.room_type_class1.id,
                "default_quota": 7,
                "default_max_avail": 0,
            }
        )

    def _service(self):
        collection = _PseudoCollection("pms.services", self.env)
        work = WorkContext(
            model_name="rest.service.registration", collection=collection
        )
        return work.component(usage="availability-plans")

    def _payload(self, **values):
        RuleInfo = self.env.datamodels["pms.availability.plan.rule.info"]
        payload = RuleInfo(partial=True)
        payload.pmsPropertyId = self.property_bridge.id
        payload.roomTypeId = self.room_type_bridge.id
        payload.availabilityPlanId = self.availability_plan1.id
        payload.date = fields.Date.to_string(self.today)
        for name, value in values.items():
            setattr(payload, name, value)
        return payload

    def _bridge(self, *payloads, **values):
        """Run the bridge over a batch, defaulting to a single night."""
        self._service()._bridge_inventory(list(payloads) or [self._payload(**values)])
        return self.env["pms.inventory.rule"].search(
            [("pms_property_id", "=", self.property_bridge.id)]
        )

    def _night(self, offset, **values):
        date = self.today + datetime.timedelta(days=offset)
        return self._payload(date=fields.Date.to_string(date), **values)

    def test_quota_alone_keeps_the_room_type_max_avail(self):
        """
        Sending only the quota must not lift the max availability the room
        type keeps closed.
        """
        rule = self._bridge(quota=3)

        self.assertEqual(len(rule), 1)
        self.assertEqual(rule.quota, 3, "The value sent must be the one written")
        self.assertEqual(
            rule.max_avail,
            0,
            "The value not sent must keep the room type default, not -1",
        )

    def test_max_avail_alone_keeps_the_room_type_quota(self):
        """
        The same the other way round: sending only the max availability keeps
        the quota the room type defaults to.
        """
        rule = self._bridge(maxAvailability=2)

        self.assertEqual(rule.max_avail, 2)
        self.assertEqual(rule.quota, 7, "The value not sent must keep the default")

    def test_both_values_are_written(self):
        """
        With both values in the payload neither default is used.
        """
        rule = self._bridge(quota=3, maxAvailability=2)

        self.assertEqual(rule.quota, 3)
        self.assertEqual(rule.max_avail, 2)

    def test_a_second_save_does_not_touch_the_value_not_sent(self):
        """
        Once the rule is there, saving one value rewrites that value only and
        leaves the other as it was configured.
        """
        self._bridge(quota=3, maxAvailability=2)

        rule = self._bridge(quota=5)

        self.assertEqual(len(rule), 1, "The rule of the night must be rewritten")
        self.assertEqual(rule.quota, 5)
        self.assertEqual(
            rule.max_avail, 2, "What was already configured must not be replaced"
        )

    def test_a_payload_with_no_inventory_writes_nothing(self):
        """
        A plan rule payload carrying only restrictions creates no inventory
        rule at all.
        """
        self.assertFalse(self._bridge(minStay=2))

    def test_the_rule_is_written_at_the_general_scope(self):
        """
        What the calendar edits is the inventory of the property, so the rule
        carries no sale channel and no agency.
        """
        rule = self._bridge(quota=3)

        self.assertFalse(rule.sale_channel_id)
        self.assertFalse(rule.agency_id)
        self.assertEqual(rule.date_from, self.today)
        self.assertEqual(rule.date_to, self.today)

    def test_consecutive_nights_are_one_rule(self):
        """
        The payload carries one item per night, but a range painted in the
        calendar has to land as ONE rule, not as one per night.
        """
        rules = self._bridge(*[self._night(offset, quota=3) for offset in range(16)])

        self.assertEqual(len(rules), 1, "The whole range must be a single rule")
        self.assertEqual(rules.date_from, self.today)
        self.assertEqual(rules.date_to, self.today + datetime.timedelta(days=15))
        self.assertEqual(rules.quota, 3)

    def test_a_gap_in_the_nights_splits_the_rules(self):
        """
        Nights that are not consecutive cannot be one range.
        """
        rules = self._bridge(
            self._night(0, quota=3),
            self._night(1, quota=3),
            self._night(5, quota=3),
        )

        self.assertEqual(len(rules), 2)
        self.assertEqual(
            sorted(rules.mapped("date_to")),
            [
                self.today + datetime.timedelta(days=1),
                self.today + datetime.timedelta(days=5),
            ],
        )

    def test_a_change_of_value_splits_the_rules(self):
        """
        Consecutive nights with different values are different rules, even
        though they touch.
        """
        rules = self._bridge(
            self._night(0, quota=3),
            self._night(1, quota=3),
            self._night(2, quota=5),
        )

        self.assertEqual(len(rules), 2)
        self.assertEqual(
            rules.filtered(lambda rule: rule.quota == 3).date_to,
            self.today + datetime.timedelta(days=1),
        )
        self.assertEqual(
            rules.filtered(lambda rule: rule.quota == 5).date_from,
            self.today + datetime.timedelta(days=2),
        )

    def test_the_nights_of_each_room_type_are_grouped_apart(self):
        """
        A batch covering several room types gives one rule per room type, not
        one mixing them.
        """
        other = self.env["pms.room.type"].create(
            {
                "pms_property_ids": [(4, self.property_bridge.id)],
                "name": "Bridge room type 2",
                "default_code": "BRDG2",
                "class_id": self.room_type_class1.id,
            }
        )
        payloads = []
        for offset in range(3):
            payloads.append(self._night(offset, quota=3))
            night = self._night(offset, quota=3)
            night.roomTypeId = other.id
            payloads.append(night)

        rules = self._bridge(*payloads)

        self.assertEqual(len(rules), 2)
        self.assertEqual(rules.mapped("room_type_id"), self.room_type_bridge + other)
        for rule in rules:
            self.assertEqual(rule.date_from, self.today)
            self.assertEqual(rule.date_to, self.today + datetime.timedelta(days=2))
