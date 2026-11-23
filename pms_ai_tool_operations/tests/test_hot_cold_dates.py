from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged

from .common import TODAY, OperationsToolsMixin

TOOL = "pms_hot_cold_dates"


@tagged("post_install", "-at_install")
class TestHotColdDates(OperationsToolsMixin, TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_hotels()
        # Other hotel has 2 rooms: full on day 0, overbooked on day 1.
        cls._reservation(0, 1, prop=cls.other_prop)
        cls._reservation(0, 1, prop=cls.other_prop)
        cls._reservation(1, 2, prop=cls.other_prop)
        cls._reservation(1, 2, prop=cls.other_prop)
        cls._reservation(1, 2, prop=cls.other_prop, forced=True)
        # Hotel with 5 rooms: 2 rooms on day 0 (40%), 3 on day 20 (60%), the
        # nights 1 to 4 and 6 to 19 stay empty.
        cls._reservation(0, 1)
        cls._reservation(0, 1)
        cls._reservation(20, 21)
        cls._reservation(20, 21)
        cls._reservation(20, 21)
        cls._reservation(5, 6)
        cls._reservation(5, 6)
        cls._reservation(5, 6)
        cls._reservation(5, 6)
        cls._reservation(5, 6)

    def _rows(self, response):
        return {row["property_id"]: row for row in response["result"]["by_property"]}

    def test_hot_and_cold_nights(self):
        response = self._call(TOOL, user=self.user_all, days=30)
        rows = self._rows(response)
        other = rows[self.other_prop.id]
        self.assertEqual(
            [(e["date"], e["occupancy_pct"]) for e in other["hot"]],
            [(str(TODAY), 100.0), (str(self.day(1)), 150.0)],
        )
        self.assertEqual(other["hot"][1]["overbooked_rooms"], 1)
        hotel = rows[self.prop.id]
        # Night 5 is full (100%): hot.
        self.assertEqual([e["date"] for e in hotel["hot"]], [str(self.day(5))])
        # Cold: below 40% in the first 14 days; day 0 has exactly 40%.
        cold_days = [e["date"] for e in hotel["cold"]]
        self.assertNotIn(str(TODAY), cold_days)
        self.assertEqual(len(cold_days), 12)
        self.assertEqual(cold_days[0], str(self.day(1)))
        self.assertEqual(cold_days[-1], str(self.day(13)))
        self.assertNotIn(str(self.day(5)), cold_days)
        # Day 20 is beyond the cold horizon and not hot: not listed.
        self.assertEqual(hotel["cold_nights"], 12)
        self.assertEqual(response["result"]["totals"]["hot_property_nights"], 3)

    def test_thresholds_and_horizon(self):
        response = self._call(
            TOOL,
            days=30,
            hot_threshold_pct=60,
            cold_threshold_pct=10,
            cold_horizon_days=30,
        )
        (row,) = response["result"]["by_property"]
        self.assertEqual(
            [e["date"] for e in row["hot"]], [str(self.day(5)), str(self.day(20))]
        )
        # Occupancy 0 below 10%, from day 1 to 29 except days 5 and 20.
        self.assertEqual(row["cold_nights"], 27)

    def test_nothing_to_report_and_scope(self):
        response = self._call(TOOL, days=1, hot_threshold_pct=101, cold_threshold_pct=0)
        self.assertEqual(response["result"]["by_property"], [])
        self.assertEqual(
            self._call(TOOL, days=2)["period"],
            {
                "date_from": str(TODAY),
                "date_to": str(self.day(1)),
                "cold_date_to": str(self.day(1)),
            },
        )
        scoped = self._call(TOOL, days=3)
        self.assertEqual(list(self._rows(scoped)), self.prop.ids)

    def test_arguments_are_validated(self):
        for kwargs in (
            {"days": 91},
            {"days": 0},
            {"cold_horizon_days": 0},
            {"cold_threshold_pct": 101},
            {"hot_threshold_pct": "95"},
            {"domain": "[]"},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(UserError):
                self._call(TOOL, **kwargs)
