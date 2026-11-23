from freezegun import freeze_time

from odoo.exceptions import AccessError, UserError
from odoo.tests.common import TransactionCase, tagged

from .common import RevenueFixture

TOOL = "pms_negative_pickup"


@tagged("post_install", "-at_install")
class TestNegativePickup(RevenueFixture, TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_revenue()

    def _negative(self, now="2026-09-18 10:00:00", **kwargs):
        with freeze_time(now):
            return self._run(TOOL, **kwargs)

    def test_default_day_is_yesterday(self):
        response = self._negative()
        self.assertEqual(response["period"]["booking_day"], "2026-09-17")
        self.assertEqual(response["period"]["stay_from"], "2026-09-17")
        # Six months counting the one of the booking day: until the end of February.
        self.assertEqual(response["period"]["stay_to"], "2027-02-28")

    def test_only_the_property_with_negative_pickup_is_listed(self):
        response = self._negative()
        # On the 17th the first hotel lost a booking of 2 nights made before
        # (cancelled that day); the second hotel booked 2 nights: positive.
        (row,) = response["result"]["by_property"]
        self.assertEqual(row["property_id"], self.prop1.id)
        self.assertEqual(row["booked_nights"], 0)
        self.assertEqual(row["cancelled_nights"], 2)
        self.assertEqual(row["net_nights"], -2)
        self.assertEqual(row["net_revenue"], -200.0)
        self.assertEqual(
            [
                (m["month"], m["net_nights"], m["net_revenue"])
                for m in row["negative_months"]
            ],
            [("2026-09", -2, -200.0)],
        )
        totals = response["result"]["totals"]
        self.assertEqual(
            (totals["properties"], totals["net_nights"], totals["net_revenue"]),
            (1, -2, -200.0),
        )
        self.assertIn(
            "1 properties with negative net pickup on 2026-09-17", response["summary"]
        )

    def test_explicit_day_and_undated_warning(self):
        response = self._negative(booking_day="2026-09-18")
        (row,) = response["result"]["by_property"]
        self.assertEqual(row["net_nights"], -1)
        self.assertIn("no cancel date", " ".join(response["warnings"]))

    def test_threshold_and_horizon(self):
        self.assertEqual(self._negative(min_net_nights=3)["result"]["by_property"], [])
        self.assertEqual(
            self._negative(min_net_nights=2)["result"]["totals"]["properties"], 1
        )
        # A horizon of one month ends on 30 September and still holds the stays.
        short = self._negative(horizon_months=1)
        self.assertEqual(short["period"]["stay_to"], "2026-09-30")
        self.assertEqual(short["result"]["totals"]["net_nights"], -2)

    def test_day_without_movement_and_scope(self):
        response = self._negative(booking_day="2026-08-01")
        self.assertEqual(response["result"]["by_property"], [])
        with self.assertRaises(AccessError):
            self._negative(property_ids=[self.prop3.id])

    def test_arguments_are_validated(self):
        for kwargs in (
            {"horizon_months": 0},
            {"horizon_months": 13},
            {"min_net_nights": 0},
            {"booking_day": "17/09/2026"},
            {"domain": "[]"},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(UserError):
                self._negative(**kwargs)
