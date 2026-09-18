# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
"""The sale restrictions are ranges on this side and days on Wubook's.

What is covered here is that translation in both directions, which is the
only place where the two shapes meet.
"""
from datetime import date, timedelta

from odoo.tests.common import tagged

from odoo.addons.component.tests.common import TransactionComponentCase

from .test_master_sync import _make_backend_environment

ROOM_EXTERNAL_ID = 111


def _wubook_day(day, **overrides):
    values = {
        "id_room": ROOM_EXTERNAL_ID,
        "date": day,
        "min_stay": 0,
        "min_stay_arrival": 0,
        "max_stay": 0,
        "closed": False,
        "closed_arrival": False,
        "closed_departure": False,
    }
    values.update(overrides)
    return values


class RangeCase(TransactionComponentCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        _make_backend_environment(cls)
        cls.room_type_a_binding = cls.env["channel.wubook.pms.room.type"].create(
            {
                "odoo_id": cls.room_type_a.id,
                "backend_id": cls.backend.id,
                "external_id": ROOM_EXTERNAL_ID,
            }
        )
        cls.plan = cls.env["pms.availability.plan"].create({"name": "Ranges plan"})
        cls.plan_binding = cls.env["channel.wubook.pms.availability.plan"].create(
            {
                "odoo_id": cls.plan.id,
                "backend_id": cls.backend.id,
                "external_id": 8001,
            }
        )
        cls.today = date.today()

    def _make_rule(self, date_from, date_to, **values):
        return (
            self.env["pms.availability.plan.rule"]
            .with_context(connector_no_export=True)
            .create(
                {
                    "availability_plan_id": self.plan.id,
                    "room_type_id": self.room_type_a.id,
                    "pms_property_id": self.pms_property.id,
                    "date_from": date_from,
                    "date_to": date_to,
                    **values,
                }
            )
        )


@tagged("post_install", "-at_install")
class TestPlanRuleRangeExport(RangeCase):
    def _items(self):
        with self.backend.work_on("channel.wubook.pms.availability.plan") as work:
            mapper = work.component(usage="export.mapper")
        values = mapper.map_record(self.plan_binding).values()
        return values.get("items") or []

    def _stage(self, date_from, date_to):
        self.plan_binding.with_context(
            connector_no_export=True
        )._wubook_stage_pending_window(date_from, date_to)

    def test_range_is_expanded_to_one_item_per_night(self):
        self._make_rule(
            self.today + timedelta(days=10), self.today + timedelta(days=12), min_stay=3
        )
        self._stage(self.today + timedelta(days=10), self.today + timedelta(days=12))
        items = self._items()
        self.assertEqual(len(items), 3)
        self.assertEqual({item["min_stay"] for item in items}, {3})
        self.assertEqual(
            sorted(item["date"] for item in items),
            [self.today + timedelta(days=i) for i in (10, 11, 12)],
        )

    def test_uncovered_night_is_sent_as_unrestricted(self):
        """A night no rule covers has to travel too, or a restriction that
        was lifted would stay on the channel forever."""
        self._make_rule(
            self.today + timedelta(days=10),
            self.today + timedelta(days=10),
            closed=True,
        )
        self._stage(self.today + timedelta(days=9), self.today + timedelta(days=11))
        by_date = {item["date"]: item for item in self._items()}
        self.assertEqual(len(by_date), 3)
        self.assertTrue(by_date[self.today + timedelta(days=10)]["closed"])
        self.assertFalse(by_date[self.today + timedelta(days=9)]["closed"])
        self.assertFalse(by_date[self.today + timedelta(days=11)]["closed"])

    def test_overlapping_ranges_send_the_last_written_one(self):
        self._make_rule(
            self.today + timedelta(days=10), self.today + timedelta(days=20), min_stay=7
        )
        self._make_rule(
            self.today + timedelta(days=12), self.today + timedelta(days=13), min_stay=2
        )
        self._stage(self.today + timedelta(days=10), self.today + timedelta(days=14))
        by_date = {item["date"]: item["min_stay"] for item in self._items()}
        self.assertEqual(by_date[self.today + timedelta(days=11)], 7)
        self.assertEqual(by_date[self.today + timedelta(days=12)], 2)
        self.assertEqual(by_date[self.today + timedelta(days=13)], 2)
        self.assertEqual(by_date[self.today + timedelta(days=14)], 7)

    def test_range_past_the_ceiling_is_trimmed_not_dropped(self):
        """A season can run past the two years Wubook accepts. Discarding it
        would stop exporting the part that is in force."""
        self._make_rule(
            self.today + timedelta(days=700),
            self.today + timedelta(days=800),
            closed=True,
        )
        self._stage(self.today + timedelta(days=700), self.today + timedelta(days=800))
        items = self._items()
        self.assertEqual(
            max(item["date"] for item in items), self.today + timedelta(days=730)
        )
        self.assertEqual(
            min(item["date"] for item in items), self.today + timedelta(days=700)
        )

    def test_window_entirely_outside_exports_nothing(self):
        self._make_rule(
            self.today - timedelta(days=40), self.today - timedelta(days=30)
        )
        self._stage(self.today - timedelta(days=40), self.today - timedelta(days=30))
        self.assertFalse(self._items())

    def test_room_type_the_plan_never_configured_is_left_alone(self):
        self._stage(self.today, self.today + timedelta(days=2))
        self.assertFalse(self._items())

    def test_a_child_plan_exports_what_it_inherits(self):
        """A plan that inherits configures what its parents configure. The
        room type is only in the parent here, and it still has to travel."""
        parent = self.env["pms.availability.plan"].create({"name": "Parent plan"})
        self.plan.parent_id = parent
        self.env["pms.availability.plan.rule"].with_context(
            connector_no_export=True
        ).create(
            {
                "availability_plan_id": parent.id,
                "room_type_id": self.room_type_a.id,
                "pms_property_id": self.pms_property.id,
                "date_from": self.today + timedelta(days=10),
                "date_to": self.today + timedelta(days=12),
                "min_stay": 4,
            }
        )
        self._stage(self.today + timedelta(days=10), self.today + timedelta(days=12))
        items = self._items()
        self.assertEqual(len(items), 3)
        self.assertEqual({item["min_stay"] for item in items}, {4})

    def test_a_child_rule_beats_the_inherited_one_on_export(self):
        parent = self.env["pms.availability.plan"].create({"name": "Parent plan"})
        self.plan.parent_id = parent
        self.env["pms.availability.plan.rule"].with_context(
            connector_no_export=True
        ).create(
            {
                "availability_plan_id": parent.id,
                "room_type_id": self.room_type_a.id,
                "pms_property_id": self.pms_property.id,
                "date_from": self.today + timedelta(days=10),
                "date_to": self.today + timedelta(days=12),
                "min_stay": 4,
            }
        )
        self._make_rule(
            self.today + timedelta(days=11), self.today + timedelta(days=11), min_stay=9
        )
        self._stage(self.today + timedelta(days=10), self.today + timedelta(days=12))
        by_date = {item["date"]: item["min_stay"] for item in self._items()}
        self.assertEqual(by_date[self.today + timedelta(days=10)], 4)
        self.assertEqual(by_date[self.today + timedelta(days=11)], 9)
        self.assertEqual(by_date[self.today + timedelta(days=12)], 4)


@tagged("post_install", "-at_install")
class TestPlanRuleRangeImport(RangeCase):
    def _rule_ops(self, items):
        with self.backend.work_on("channel.wubook.pms.availability.plan") as work:
            mapper = work.component(usage="import.mapper")
        values = mapper.map_record(
            {"id": 8001, "name": "Ranges plan", "items": items}
        ).values(binding=self.plan_binding)
        return values.get("rule_ids") or []

    def test_equal_consecutive_days_collapse_into_one_range(self):
        items = [
            _wubook_day(self.today + timedelta(days=i), min_stay=2) for i in range(3)
        ]
        ops = self._rule_ops(items)
        self.assertEqual(len(ops), 1)
        op, _id, values = ops[0]
        self.assertEqual(op, 0)
        self.assertEqual(values["date_from"], self.today)
        self.assertEqual(values["date_to"], self.today + timedelta(days=2))
        self.assertEqual(values["min_stay"], 2)

    def test_a_day_with_other_values_breaks_the_range(self):
        items = [
            _wubook_day(self.today, min_stay=2),
            _wubook_day(self.today + timedelta(days=1), min_stay=5),
            _wubook_day(self.today + timedelta(days=2), min_stay=2),
        ]
        ops = self._rule_ops(items)
        self.assertEqual(len(ops), 3)
        self.assertEqual(
            sorted(
                (values["date_from"], values["min_stay"]) for _op, _id, values in ops
            ),
            [
                (self.today, 2),
                (self.today + timedelta(days=1), 5),
                (self.today + timedelta(days=2), 2),
            ],
        )

    def test_a_missing_day_breaks_the_range(self):
        items = [
            _wubook_day(self.today, min_stay=2),
            _wubook_day(self.today + timedelta(days=2), min_stay=2),
        ]
        ops = self._rule_ops(items)
        self.assertEqual(len(ops), 2)

    def test_reimporting_the_same_window_updates_in_place(self):
        rule = self._make_rule(self.today, self.today + timedelta(days=2), min_stay=2)
        items = [
            _wubook_day(self.today + timedelta(days=i), min_stay=4) for i in range(3)
        ]
        ops = self._rule_ops(items)
        self.assertEqual(len(ops), 1)
        op, rule_id, values = ops[0]
        self.assertEqual(op, 1)
        self.assertEqual(rule_id, rule.id)
        self.assertEqual(values["min_stay"], 4)


@tagged("post_install", "-at_install")
class TestPropertyAvailabilityExport(RangeCase):
    """What Wubook is told a property has for sale, night by night.

    There is no record per night behind it any more: the number is resolved
    from the physical availability and the inventory declared for the night,
    and a night nothing has touched has neither.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.rooms = cls.env["pms.room"].create(
            [
                {
                    "name": f"Ranges 10{index}",
                    # Set explicitly: created in one batch, the autocompleted
                    # short name would be the same for both and collide.
                    "short_name": f"RG0{index}",
                    "pms_property_id": cls.pms_property.id,
                    "room_type_id": cls.room_type_a.id,
                    "capacity": 2,
                }
                for index in range(2)
            ]
        )
        # The ceiling the connector applies when no rule declares one. It is
        # resolved once, when the binding is created, so it has to be asked
        # for again now that the room type has rooms.
        cls.room_type_a_binding.default_max_avail = -1
        cls.property_binding = cls.env[
            "channel.wubook.pms.property.availability"
        ].create(
            {
                "odoo_id": cls.pms_property.id,
                "backend_id": cls.backend.id,
                "external_id": cls.pms_property.id,
            }
        )

    def _stage(self, date_from, date_to):
        self.property_binding.with_context(
            connector_no_export=True
        )._wubook_stage_pending_window(date_from, date_to)

    def _items(self):
        with self.backend.work_on("channel.wubook.pms.property.availability") as work:
            mapper = work.component(usage="export.mapper")
        values = mapper.map_record(self.property_binding).values()
        return values.get("availabilities") or []

    def _make_inventory_rule(self, date_from, date_to, **values):
        return (
            self.env["pms.inventory.rule"]
            .with_context(connector_no_export=True)
            .create(
                {
                    "pms_property_id": self.pms_property.id,
                    "room_type_id": self.room_type_a.id,
                    "date_from": date_from,
                    "date_to": date_to,
                    **values,
                }
            )
        )

    def test_a_night_with_no_record_still_travels(self):
        """The regression this replaces: nothing materializes a row for a
        night nobody booked, and it has rooms for sale all the same."""
        self._stage(self.today + timedelta(days=10), self.today + timedelta(days=12))
        self.assertFalse(
            self.env["pms.availability"].search(
                [
                    ("pms_property_id", "=", self.pms_property.id),
                    ("date", ">=", self.today + timedelta(days=10)),
                ]
            )
        )
        items = self._items()
        self.assertEqual(len(items), 3)
        self.assertEqual({item["avail"] for item in items}, {2})
        self.assertEqual({item["id_room"] for item in items}, {ROOM_EXTERNAL_ID})

    def test_declared_inventory_caps_what_is_published(self):
        self._make_inventory_rule(
            self.today + timedelta(days=10),
            self.today + timedelta(days=11),
            max_avail=1,
        )
        self._stage(self.today + timedelta(days=10), self.today + timedelta(days=12))
        by_date = {item["date"]: item["avail"] for item in self._items()}
        self.assertEqual(by_date[self.today + timedelta(days=10)], 1)
        self.assertEqual(by_date[self.today + timedelta(days=11)], 1)
        # Out of the rule: both rooms are for sale again.
        self.assertEqual(by_date[self.today + timedelta(days=12)], 2)

    def test_the_connector_default_is_the_ceiling_with_no_rule(self):
        """No rule declares anything, so what is published is the default
        the connector holds for the room type, not the room count."""
        self.room_type_a_binding.default_max_avail = 1
        self._stage(self.today + timedelta(days=10), self.today + timedelta(days=10))
        self.assertEqual([item["avail"] for item in self._items()], [1])

    def test_window_is_trimmed_to_what_wubook_accepts(self):
        self._stage(self.today + timedelta(days=725), self.today + timedelta(days=800))
        items = self._items()
        self.assertEqual(
            max(item["date"] for item in items), self.today + timedelta(days=730)
        )

    def test_nothing_pending_pushes_the_whole_window(self):
        """A property connected for the first time has no window staged and
        has to be told everything."""
        items = self._items()
        self.assertEqual(
            min(item["date"] for item in items), self.today - timedelta(days=2)
        )
        self.assertEqual(
            max(item["date"] for item in items), self.today + timedelta(days=730)
        )

    def test_a_room_type_not_sold_here_is_left_alone(self):
        self.rooms.unlink()
        self._stage(self.today, self.today + timedelta(days=1))
        self.assertFalse(self._items())
