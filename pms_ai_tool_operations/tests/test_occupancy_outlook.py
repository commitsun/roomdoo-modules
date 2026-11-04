from odoo.exceptions import AccessError, UserError
from odoo.tests.common import TransactionCase, tagged

from .common import TODAY, OperationsToolsMixin

TOOL = "pms_occupancy_outlook"


@tagged("post_install", "-at_install")
class TestOccupancyOutlook(OperationsToolsMixin, TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_hotels()
        # Hotel: 5 rooms for overnight stays and a hall.
        cls._reservation(0, 2)
        cls._reservation(1, 2)
        cls._reservation(0, 1, state="draft")  # pre-reservation: not counted
        cls._reservation(0, 1, state="cancel")  # cancelled: not counted
        cls._reservation(0, 1, rtype="staff")
        cls._reservation(1, 3, rtype="out")  # blocks a room the nights 1 and 2
        cls._reservation(0, 1, room_type=cls.meeting_type, adults=None)  # hall
        # Other hotel: 2 rooms.
        cls._reservation(0, 1, prop=cls.other_prop)
        cls._reservation(0, 1, prop=cls.other_prop)
        cls._reservation(1, 2, prop=cls.other_prop)
        cls._reservation(1, 2, prop=cls.other_prop)
        cls._reservation(1, 2, prop=cls.other_prop, forced=True)  # overbooked
        cls._reservation(2, 3, prop=cls.other_prop)
        cls._reservation(2, 3, prop=cls.other_prop, rtype="out")

    def _rows(self, response):
        return {row["property_id"]: row for row in response["result"]["by_property"]}

    def _days(self, row):
        return {entry["date"]: entry for entry in row["days"]}

    def test_occupancy_of_the_hotel(self):
        response = self._call(TOOL, date_from=str(TODAY), days=5)
        (row,) = response["result"]["by_property"]
        self.assertEqual(row["rooms"], 5)
        self.assertEqual(len(row["days"]), 5)
        days = self._days(row)
        self.assertEqual(
            days[str(TODAY)],
            {
                "date": str(TODAY),
                "occupied": 2,
                "blocked": 0,
                "available": 3,
                "occupancy_pct": 40.0,
            },
        )
        self.assertEqual(
            days[str(self.day(1))],
            {
                "date": str(self.day(1)),
                "occupied": 2,
                "blocked": 1,
                "available": 2,
                "occupancy_pct": 40.0,
            },
        )
        self.assertEqual(
            days[str(self.day(2))],
            {
                "date": str(self.day(2)),
                "occupied": 0,
                "blocked": 1,
                "available": 4,
                "occupancy_pct": 0.0,
            },
        )
        self.assertEqual(days[str(self.day(4))]["available"], 5)
        self.assertEqual((row["high_days"], row["overbooked_days"]), (0, 0))
        self.assertEqual((row["peak_pct"], row["peak_date"]), (40.0, str(TODAY)))
        self.assertIn(
            "pre-reservations (not confirmed) in the period are not counted: 1.",
            " ".join(response["warnings"]),
        )

    def test_flags_high_and_overbooked_nights(self):
        response = self._call(TOOL, user=self.user_all, date_from=str(TODAY), days=5)
        rows = self._rows(response)
        other = rows[self.other_prop.id]
        self.assertEqual(other["rooms"], 2)
        days = self._days(other)
        full = days[str(TODAY)]
        self.assertEqual((full["occupied"], full["occupancy_pct"]), (2, 100.0))
        self.assertEqual((full["flag"], full["available"]), ("high", 0))
        over = days[str(self.day(1))]
        self.assertEqual((over["occupied"], over["occupancy_pct"]), (3, 150.0))
        self.assertEqual(
            (over["flag"], over["overbooked_rooms"], over["available"]),
            ("overbooking", 1, 0),
        )
        blocked = days[str(self.day(2))]
        self.assertEqual(
            (blocked["occupied"], blocked["blocked"], blocked["occupancy_pct"]),
            (1, 1, 50.0),
        )
        # Every room is in use, but nothing is overbooked and it is not busy.
        self.assertNotIn("flag", blocked)
        self.assertEqual((other["high_days"], other["overbooked_days"]), (1, 1))
        self.assertEqual(
            (other["peak_pct"], other["peak_date"]), (150.0, str(self.day(1)))
        )
        # The riskiest hotel goes first.
        self.assertEqual(
            [row["property_id"] for row in response["result"]["by_property"]],
            [self.other_prop.id, self.prop.id],
        )
        self.assertEqual(
            response["result"]["totals"],
            {
                "properties": 2,
                "overbooked_property_days": 1,
                "high_property_days": 1,
            },
        )
        self.assertIn("1 property days overbooked and 1 more", response["summary"])

    def test_hall_is_not_a_room_for_overnight_stays(self):
        # The hall has a reservation the night 0 and a room, and counts for nothing.
        (row,) = self._call(TOOL, date_from=str(TODAY), days=1)["result"]["by_property"]
        self.assertEqual(row["rooms"], 5)
        self.assertEqual(row["days"][0]["occupied"], 2)

    def test_default_period_is_14_days_from_today(self):
        response = self._call(TOOL)
        self.assertEqual(
            response["period"],
            {"date_from": str(TODAY), "date_to": str(self.day(13))},
        )
        self.assertEqual(len(response["result"]["by_property"][0]["days"]), 14)
        one = self._call(TOOL, days=1)
        self.assertEqual(len(one["result"]["by_property"][0]["days"]), 1)

    def test_scope_of_the_user(self):
        response = self._call(TOOL, days=2)
        self.assertEqual(list(self._rows(response)), self.prop.ids)
        with self.assertRaises(AccessError):
            self._call(TOOL, property_ids=self.other_prop.ids)
        only_other = self._call(
            TOOL, user=self.user_all, days=2, property_ids=self.other_prop.ids
        )
        self.assertEqual(list(self._rows(only_other)), self.other_prop.ids)

    def test_property_without_rooms_has_no_percentage(self):
        empty = self.env["pms.property"].create(
            {
                "name": "AI ops empty",
                "company_id": self.env.company.id,
                "default_pricelist_id": self.prop.default_pricelist_id.id,
            }
        )
        self.user_all.pms_property_ids |= empty
        response = self._call(TOOL, user=self.user_all, days=1, property_ids=empty.ids)
        (row,) = response["result"]["by_property"]
        self.assertEqual(row["rooms"], 0)
        self.assertIsNone(row["days"][0]["occupancy_pct"])
        self.assertNotIn("flag", row["days"][0])
        self.assertIn(
            "AI ops empty has no rooms for overnight stays",
            " ".join(response["warnings"]),
        )

    def test_envelope(self):
        response = self._call(TOOL, days=3)
        self.assertEqual(response["environment"], "test")
        self.assertEqual(response["tool"], TOOL)
        self.assertEqual(
            response["period"], {"date_from": str(TODAY), "date_to": str(self.day(2))}
        )
        self.assertEqual(len(response["definitions"]), 4)
        self.assertTrue(response["generated_at"])
        self.assertEqual(
            response["summary"],
            "0 property days overbooked and 0 more at 95% or above between "
            f"{TODAY} and {self.day(2)}.",
        )

    def test_arguments_are_validated(self):
        for kwargs in (
            {"days": 0},
            {"days": 32},
            {"days": "7"},
            {"days": True},
            {"date_from": "2026/09/21"},
            {"domain": "[]"},
            {"property_ids": [1.5]},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(UserError):
                self._call(TOOL, **kwargs)
