from odoo.exceptions import AccessError, UserError
from odoo.tests.common import TransactionCase, tagged

from .common import RevenueFixture


@tagged("post_install", "-at_install")
class TestChannelMix(RevenueFixture, TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_revenue()

    def _mix(self, **kwargs):
        kwargs.setdefault("date_from", "2026-09-21")
        kwargs.setdefault("date_to", "2026-09-27")
        return self._run("pms_channel_mix", **kwargs)

    def _rows(self, response):
        return {row["name"]: row for row in response["result"]["rows"]}

    def test_mix_by_channel_with_last_year(self):
        response = self._mix()
        totals = response["result"]["totals"]
        self.assertEqual(totals["room_nights"], 11)
        self.assertEqual(totals["room_revenue"], 1200.0)
        self.assertEqual(totals["last_year"]["room_nights"], 3)
        self.assertEqual(totals["last_year"]["room_revenue"], 290.0)
        rows = response["result"]["rows"]
        self.assertEqual([row["name"] for row in rows], ["AI door", "AI OTA"])
        door, ota = rows
        self.assertEqual(door["id"], self.direct.id)
        self.assertEqual(door["channel_type"], "direct")
        self.assertEqual(door["room_nights"], 7)
        self.assertEqual(door["room_nights_share_pct"], 63.6)
        self.assertEqual(door["room_revenue"], 700.0)
        self.assertEqual(door["room_revenue_share_pct"], 58.3)
        # Two of its nights are a courtesy: left out of the ADR.
        self.assertEqual(door["adr"], 140.0)
        self.assertEqual(ota["channel_type"], "indirect")
        self.assertEqual(ota["room_nights"], 4)
        self.assertEqual(ota["room_revenue"], 500.0)
        self.assertEqual(ota["room_revenue_share_pct"], 41.7)
        self.assertEqual(ota["adr"], 125.0)
        # Last year everything was sold at the door.
        self.assertEqual(door["last_year"]["room_nights_share_pct"], 100.0)
        self.assertEqual(door["last_year"]["room_revenue"], 290.0)
        self.assertEqual(ota["last_year"]["room_nights"], 0)
        self.assertEqual(ota["last_year"]["room_nights_share_pct"], 0.0)
        self.assertIsNone(ota["last_year"]["adr"])
        changes = door["change_vs_last_year"]
        self.assertEqual(changes["room_nights"], {"change": 4, "change_pct": 133.3})
        self.assertEqual(changes["room_nights_share_points"], -36.4)
        self.assertEqual(changes["room_revenue_share_points"], -41.7)
        ota_changes = ota["change_vs_last_year"]
        self.assertEqual(ota_changes["room_nights"], {"change": 4, "change_pct": None})
        self.assertEqual(ota_changes["room_nights_share_points"], 36.4)

    def test_mix_by_agency(self):
        response = self._mix(group_by="agency")
        rows = response["result"]["rows"]
        direct, agency = rows
        self.assertEqual(direct["name"], "No agency (direct sales)")
        self.assertIsNone(direct["id"])
        self.assertEqual(direct["room_nights"], 9)
        self.assertEqual(direct["room_nights_share_pct"], 81.8)
        self.assertEqual(direct["room_revenue"], 1000.0)
        self.assertEqual(agency["id"], self.agency.id)
        self.assertEqual(agency["name"], "AI Agency")
        self.assertEqual(agency["room_nights"], 2)
        self.assertEqual(agency["room_revenue"], 200.0)
        self.assertEqual(agency["room_revenue_share_pct"], 16.7)
        self.assertEqual(agency["last_year"]["room_nights"], 0)
        self.assertNotIn("channel_type", agency)
        self.assertTrue(
            any(text.startswith("Agency:") for text in response["definitions"])
        )

    def test_top_adds_up_the_rest(self):
        response = self._mix(top=1)
        door, other = response["result"]["rows"]
        self.assertEqual(door["name"], "AI door")
        self.assertEqual(other["name"], "Other (1 more)")
        self.assertTrue(other["is_other"])
        self.assertIsNone(other["id"])
        self.assertEqual(other["room_nights"], 4)
        self.assertEqual(
            door["room_revenue_share_pct"] + other["room_revenue_share_pct"], 100.0
        )

    def test_channels_that_only_sold_last_year_are_listed(self):
        # On the 24th and 25th only the OTA sold now; last year the door sold.
        response = self._mix(date_from="2026-09-24", date_to="2026-09-25")
        ota, door = response["result"]["rows"]
        self.assertEqual(ota["name"], "AI OTA")
        self.assertEqual(ota["room_nights_share_pct"], 100.0)
        self.assertEqual(door["name"], "AI door")
        self.assertEqual(door["room_nights"], 0)
        self.assertEqual(door["last_year"]["room_nights"], 1)
        self.assertEqual(door["last_year"]["room_nights_share_pct"], 100.0)

    def test_envelope(self):
        response = self._mix()
        self.assertEqual(response["tool"], "pms_channel_mix")
        self.assertEqual(response["environment"], "test")
        self.assertIn("Mix of 11 room nights by sale channel", response["summary"])
        self.assertIn("AI door with 58.3% of the revenue", response["summary"])
        self.assertEqual(
            response["period"]["last_year"],
            {"date_from": "2025-09-21", "date_to": "2025-09-27"},
        )
        definitions = " ".join(response["definitions"])
        for text in ("Channel:", "Share:", "without VAT", "Last year:"):
            self.assertIn(text, definitions)
        self.assertTrue(any("worth 0" in text for text in response["warnings"]))

    def test_scope_and_validation(self):
        with self.assertRaises(AccessError):
            self._mix(property_ids=[self.prop3.id])
        only_prop2 = self._mix(property_ids=[self.prop2.id])["result"]
        self.assertEqual(only_prop2["totals"]["room_nights"], 2)
        for kwargs in (
            {"group_by": "partner"},
            {"top": 31},
            {"top": 0},
            {"top": "3"},
            {"date_from": "2026-01-01", "date_to": "2026-04-30"},
            {"date_from": "2026-09-27", "date_to": "2026-09-21"},
            {"domain": "[]"},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(UserError):
                self._mix(**kwargs)
        self.assertTrue(self._mix(top=30))
