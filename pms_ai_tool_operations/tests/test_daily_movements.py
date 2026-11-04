from odoo.exceptions import AccessError, UserError
from odoo.tests.common import TransactionCase, new_test_user, tagged

from .common import PMS_USER_GROUPS, TODAY, OperationsToolsMixin

TOOL = "pms_daily_movements"


@tagged("post_install", "-at_install")
class TestDailyMovements(OperationsToolsMixin, TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_hotels()
        # Hotel, the day of the tool is the day 0.
        cls._reservation(0, 1, state="cancel")
        cls._reservation(0, 2)  # arrival still expected, in house the nights 0 and 1
        cls._reservation(0, 1, state="onboard", adults=1)  # arrived
        cls._reservation(-2, 0, state="onboard")  # leaves today, still on board
        cls._reservation(-3, 0, state="done", adults=1)  # left today
        cls._reservation(-1, 3, state="onboard", adults=2, children=1)  # in house
        cls._reservation(0, 1, state="draft")  # pre-reservation
        cls._reservation(0, 1, rtype="staff", adults=1)  # staff arrival: counts
        cls._reservation(0, 1, room_type=cls.meeting_type, adults=None)  # hall
        # Other hotel
        cls._reservation(0, 1, prop=cls.other_prop, adults=1)
        cls._reservation(-1, 0, prop=cls.other_prop, state="onboard", adults=1)

    def _movements(self, response, prop):
        (row,) = (
            r for r in response["result"]["by_property"] if r["property_id"] == prop.id
        )
        return row

    def test_counts_of_the_day(self):
        response = self._call(TOOL, date=str(TODAY))
        (row,) = response["result"]["by_property"]
        self.assertEqual(row["property"], "AI ops hotel")
        self.assertEqual(
            row["arrivals"],
            {"reservations": 3, "pax": 4, "checked_in": 1, "pending": 2},
        )
        self.assertEqual(
            row["departures"],
            {"reservations": 2, "pax": 3, "checked_out": 1, "pending": 1},
        )
        self.assertEqual(row["in_house"], {"rooms": 4, "pax": 7})
        self.assertEqual(row["pre_reservations"], 1)
        self.assertEqual(response["result"]["totals"]["arrivals"], row["arrivals"])
        self.assertIn("are not counted: 1.", " ".join(response["warnings"]))

    def test_other_days_and_default_day(self):
        tomorrow = self._call(TOOL, date=str(self.day(1)))["result"]["by_property"][0]
        self.assertEqual(tomorrow["arrivals"]["reservations"], 0)
        self.assertEqual(
            tomorrow["departures"],
            {"reservations": 2, "pax": 2, "checked_out": 0, "pending": 2},
        )
        self.assertEqual(tomorrow["in_house"], {"rooms": 2, "pax": 5})
        # No date: today of the fixtures.
        default = self._call(TOOL)
        self.assertEqual(
            default["period"], {"date_from": str(TODAY), "date_to": str(TODAY)}
        )
        self.assertEqual(default["result"]["by_property"][0]["in_house"]["rooms"], 4)

    def test_default_day_is_today_in_the_timezone_of_the_user(self):
        self.user.tz = "Europe/Madrid"
        # 23:30 UTC of the 21st is already the 22nd in Madrid.
        response = self._call(TOOL, _now=f"{TODAY} 23:30:00")
        self.assertEqual(response["period"]["date_from"], str(self.day(1)))
        self.user.tz = "UTC"
        response = self._call(TOOL, _now=f"{TODAY} 23:30:00")
        self.assertEqual(response["period"]["date_from"], str(TODAY))

    def test_scope_of_the_user(self):
        response = self._call(TOOL, date=str(TODAY))
        self.assertEqual(
            [row["property_id"] for row in response["result"]["by_property"]],
            self.prop.ids,
        )
        with self.assertRaises(AccessError):
            self._call(TOOL, property_ids=self.other_prop.ids)
        both = self._call(TOOL, user=self.user_all, date=str(TODAY))
        self.assertEqual(
            {row["property_id"] for row in both["result"]["by_property"]},
            set((self.prop | self.other_prop).ids),
        )
        other = self._movements(both, self.other_prop)
        self.assertEqual(
            other["arrivals"],
            {"reservations": 1, "pax": 1, "checked_in": 0, "pending": 1},
        )
        self.assertEqual(
            other["departures"],
            {"reservations": 1, "pax": 1, "checked_out": 0, "pending": 1},
        )
        self.assertEqual(other["in_house"], {"rooms": 1, "pax": 1})
        self.assertEqual(
            both["result"]["totals"],
            {
                "arrivals": {
                    "reservations": 4,
                    "pax": 5,
                    "checked_in": 1,
                    "pending": 3,
                },
                "departures": {
                    "reservations": 3,
                    "pax": 4,
                    "checked_out": 1,
                    "pending": 2,
                },
                "in_house": {"rooms": 5, "pax": 8},
                "pre_reservations": 1,
            },
        )
        only_other = self._call(
            TOOL, user=self.user_all, date=str(TODAY), property_ids=self.other_prop.ids
        )
        self.assertEqual(
            [row["property_id"] for row in only_other["result"]["by_property"]],
            self.other_prop.ids,
        )

    def test_user_without_properties(self):
        nobody = new_test_user(self.env, login="ai_ops_nobody", groups=PMS_USER_GROUPS)
        response = self._call(TOOL, user=nobody, date=str(TODAY))
        self.assertEqual(response["result"]["by_property"], [])
        self.assertIn("no properties", " ".join(response["warnings"]))

    def test_envelope(self):
        response = self._call(TOOL, date=str(TODAY))
        self.assertEqual(response["environment"], "test")
        self.assertEqual(response["tool"], TOOL)
        self.assertEqual(
            response["period"], {"date_from": str(TODAY), "date_to": str(TODAY)}
        )
        self.assertEqual(len(response["definitions"]), 4)
        self.assertTrue(response["generated_at"])
        self.assertEqual(
            response["summary"],
            "3 arrivals (1 already checked in), 2 departures (1 already out) and "
            "4 rooms with 7 guests in house that night.",
        )

    def test_arguments_are_validated(self):
        for kwargs in (
            {"domain": "[]"},
            {"date": "21/09/2026"},
            {"date": 20260921},
            {"property_ids": ["1"]},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(UserError):
                self._call(TOOL, **kwargs)

    def test_same_rooms_as_the_occupancy_outlook(self):
        outlook = self._call("pms_occupancy_outlook", date_from=str(TODAY), days=1)
        (row,) = outlook["result"]["by_property"]
        movements = self._call(TOOL, date=str(TODAY))["result"]["totals"]
        self.assertEqual(row["days"][0]["occupied"], movements["in_house"]["rooms"])
