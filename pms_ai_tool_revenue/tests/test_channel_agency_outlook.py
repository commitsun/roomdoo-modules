from freezegun import freeze_time

from odoo.exceptions import AccessError, UserError
from odoo.tests.common import TransactionCase, tagged

from .common import RevenueFixture

TOOL = "pms_channel_agency_outlook"


@tagged("post_install", "-at_install")
class TestChannelAgencyOutlook(RevenueFixture, TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_revenue()

    def _outlook(self, **kwargs):
        with freeze_time("2026-09-20 10:00:00"):
            return self._run(TOOL, **kwargs)

    def test_totals_and_period(self):
        response = self._outlook()
        totals = response["result"]["totals"]
        self.assertEqual(totals["room_nights"], 11)
        self.assertEqual(totals["room_revenue"], 1200.0)
        # Last year at this same moment: the same figures as the pace tool.
        self.assertEqual(totals["last_year_same_time"]["room_nights"], 3)
        self.assertEqual(totals["last_year_same_time"]["room_revenue"], 280.0)
        period = response["period"]
        self.assertEqual(period["date_from"], "2026-09-20")
        self.assertEqual(period["date_to"], "2026-12-18")
        self.assertEqual(
            period["last_year"], {"date_from": "2025-09-20", "date_to": "2025-12-18"}
        )

    def test_by_channel(self):
        rows = {r["name"]: r for r in self._outlook()["result"]["by_channel"]}
        door, ota = rows["AI door"], rows["AI OTA"]
        self.assertEqual((door["room_nights"], door["room_revenue"]), (7, 700.0))
        self.assertEqual(door["room_revenue_share_pct"], 58.3)
        self.assertEqual(door["last_year_same_time"]["room_nights"], 3)
        self.assertEqual(door["last_year_same_time"]["room_revenue"], 280.0)
        self.assertEqual((ota["room_nights"], ota["room_revenue"]), (4, 500.0))
        self.assertEqual(ota["last_year_same_time"]["room_nights"], 0)
        self.assertEqual(ota["adr"], 125.0)

    def test_by_agency(self):
        rows = {r["name"]: r for r in self._outlook()["result"]["by_agency"]}
        agency = rows["AI Agency"]
        self.assertEqual((agency["room_nights"], agency["room_revenue"]), (2, 200.0))
        direct = rows["No agency (direct sales)"]
        self.assertEqual((direct["room_nights"], direct["room_revenue"]), (9, 1000.0))
        self.assertEqual(direct["last_year_same_time"]["room_nights"], 3)

    def test_days_limit_the_period_and_top_groups_the_rest(self):
        short = self._outlook(days=1)
        self.assertEqual(short["result"]["totals"]["room_nights"], 0)
        self.assertEqual(short["period"]["date_to"], "2026-09-20")
        top = self._outlook(top=1)["result"]["by_channel"]
        self.assertEqual([r["name"] for r in top], ["AI door", "Other (1 more)"])
        self.assertTrue(top[1]["is_other"])

    def test_scope_and_arguments(self):
        with self.assertRaises(AccessError):
            self._outlook(property_ids=[self.prop3.id])
        for kwargs in ({"days": 0}, {"days": 94}, {"top": 31}, {"domain": "[]"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(UserError):
                self._outlook(**kwargs)
