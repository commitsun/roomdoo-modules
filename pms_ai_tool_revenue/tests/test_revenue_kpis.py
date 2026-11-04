from datetime import date, timedelta

from freezegun import freeze_time

from odoo.exceptions import AccessError, UserError
from odoo.tests.common import TransactionCase, tagged

from .common import RevenueFixture


@tagged("post_install", "-at_install")
class TestRevenueKpis(RevenueFixture, TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_revenue()

    def _kpis(self, **kwargs):
        kwargs.setdefault("date_from", "2026-09-21")
        kwargs.setdefault("date_to", "2026-09-27")
        return self._run("pms_revenue_kpis", **kwargs)

    def test_totals_and_last_year(self):
        response = self._kpis()
        totals = response["result"]["totals"]
        self.assertEqual(totals["room_nights_sold"], 11)
        self.assertEqual(totals["room_nights_capacity"], 45)
        self.assertEqual(totals["out_of_service_nights"], 2)
        self.assertEqual(totals["staff_nights"], 1)
        self.assertEqual(totals["room_nights_available"], 42)
        self.assertEqual(totals["occupancy_pct"], 26.2)
        self.assertEqual(totals["room_revenue"], 1200.0)
        self.assertEqual(totals["room_revenue_incl_vat"], 1320.0)
        # 2 of the 11 nights are a courtesy: they are not in the ADR.
        self.assertEqual(totals["adr"], 133.33)
        self.assertEqual(totals["revpar"], 28.57)
        self.assertEqual(totals["zero_value_nights"], 2)

        before = totals["last_year"]
        self.assertEqual(before["room_nights_sold"], 3)
        self.assertEqual(before["room_nights_available"], 45)
        self.assertEqual(before["occupancy_pct"], 6.7)
        self.assertEqual(before["room_revenue"], 290.0)
        self.assertEqual(before["room_revenue_incl_vat"], 319.0)
        self.assertEqual(before["adr"], 96.67)
        self.assertEqual(before["revpar"], 6.44)
        changes = totals["vs_last_year"]
        self.assertEqual(
            changes["room_nights_sold"], {"change": 8, "change_pct": 266.7}
        )
        self.assertEqual(changes["occupancy_pct"], {"change_points": 19.5})
        self.assertEqual(
            changes["room_revenue"], {"change": 910.0, "change_pct": 313.8}
        )
        self.assertEqual(changes["adr"], {"change": 36.66, "change_pct": 37.9})

    def test_figures_by_property(self):
        rows = self._by_property(self._kpis())
        self.assertEqual(list(rows), [self.prop1.id, self.prop2.id])
        prop1 = rows[self.prop1.id]
        self.assertEqual(prop1["property"], self.prop1.name)
        self.assertEqual(prop1["room_nights_sold"], 9)
        self.assertEqual(prop1["room_nights_capacity"], 28)
        self.assertEqual(prop1["room_nights_available"], 25)
        self.assertEqual(prop1["occupancy_pct"], 36.0)
        self.assertEqual(prop1["room_revenue"], 800.0)
        self.assertEqual(prop1["adr"], 114.29)
        self.assertEqual(prop1["revpar"], 32.0)
        self.assertEqual(prop1["last_year"]["room_nights_available"], 31)
        self.assertEqual(prop1["last_year"]["occupancy_pct"], 9.7)
        self.assertEqual(prop1["last_year"]["revpar"], 9.35)
        prop2 = rows[self.prop2.id]
        self.assertEqual(prop2["room_nights_sold"], 2)
        self.assertEqual(prop2["occupancy_pct"], 11.8)
        self.assertEqual(prop2["adr"], 200.0)
        self.assertEqual(prop2["revpar"], 23.53)
        last_year = prop2["last_year"]
        self.assertEqual(last_year["room_nights_sold"], 0)
        self.assertEqual(last_year["occupancy_pct"], 0.0)
        self.assertIsNone(last_year["adr"])

    def test_rooms_count_while_they_existed(self):
        # The room created on the 25th counts from that night (3 of 7); the
        # archived one counts last year until its last occupied night (3 of 7);
        # the meeting room never counts.
        prop2 = self._kpis(property_ids=[self.prop2.id])["result"]["totals"]
        self.assertEqual(prop2["room_nights_capacity"], 2 * 7 + 3)
        self.assertEqual(prop2["last_year"]["room_nights_capacity"], 2 * 7)
        prop1 = self._kpis(property_ids=[self.prop1.id])["result"]["totals"]
        self.assertEqual(prop1["room_nights_capacity"], 4 * 7)
        self.assertEqual(prop1["last_year"]["room_nights_capacity"], 4 * 7 + 3)

    def test_without_comparison(self):
        response = self._kpis(compare_last_year=False)
        totals = response["result"]["totals"]
        self.assertEqual(totals["room_nights_sold"], 11)
        self.assertNotIn("last_year", totals)
        self.assertNotIn("vs_last_year", totals)
        for row in response["result"]["by_property"]:
            self.assertNotIn("last_year", row)
        self.assertNotIn("last_year", response["period"])

    def test_revenue_without_vat_matches_the_reservation(self):
        response = self._kpis(
            date_to="2026-09-23",
            property_ids=[self.prop1.id],
            compare_last_year=False,
        )
        totals = response["result"]["totals"]
        # Only the first reservation has overnight nights on those dates.
        self.assertEqual(totals["room_nights_sold"], 3)
        self.assertEqual(totals["room_revenue"], self.res_a.price_subtotal)
        self.assertEqual(totals["room_revenue_incl_vat"], self.res_a.price_total)
        self.assertEqual(totals["room_revenue"], 300.0)

    def test_reservations_without_taxes_are_flagged(self):
        self.env.cr.execute(
            "DELETE FROM account_tax_pms_reservation_rel WHERE pms_reservation_id = %s",
            [self.res_a.id],
        )
        response = self._kpis(
            date_to="2026-09-23",
            property_ids=[self.prop1.id],
            compare_last_year=False,
        )
        # Its price is taken as already without VAT.
        self.assertEqual(response["result"]["totals"]["room_revenue"], 330.0)
        self.assertTrue(any("no taxes" in text for text in response["warnings"]))

    def test_envelope(self):
        response = self._kpis()
        self.assertEqual(response["environment"], "test")
        self.assertEqual(response["tool"], "pms_revenue_kpis")
        self.assertTrue(response["generated_at"])
        self.assertTrue(response["summary"].startswith("Occupancy 26.2% (11 of 42"))
        self.assertIn("Last year: occupancy 6.7%", response["summary"])
        self.assertEqual(response["period"]["days"], 7)
        self.assertEqual(
            response["period"]["last_year"],
            {"date_from": "2025-09-21", "date_to": "2025-09-27"},
        )
        definitions = " ".join(response["definitions"])
        for text in ("Room night:", "Room nights available", "Occupancy:", "ADR:"):
            self.assertIn(text, definitions)
        self.assertIn("without VAT", definitions)
        self.assertTrue(
            any("worth 0" in text for text in response["warnings"]),
            response["warnings"],
        )

    def test_occupancy_above_100_is_flagged(self):
        prop = self.env["pms.property"].create(
            {
                "name": "AI crowded property",
                "company_id": self.env.company.id,
                "default_pricelist_id": self.prop1.default_pricelist_id.id,
            }
        )
        self.room_type.pms_property_ids = [(4, prop.id)]
        first = self._room(prop, "First room", "ONE")
        second = self._room(prop, "Second room", "TWO")
        self.user.pms_property_ids = [(4, prop.id)]
        nights = self._nights(2026, 22, [(100, 0)])
        with freeze_time("2026-09-01"):
            self._reservation(prop, self.direct, nights)
            self._reservation(prop, self.direct, nights)
        # Overbooking as found in real data: both reservations on the same
        # room, while the other room did not exist yet that night.
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE pms_reservation_line SET room_id = %s WHERE room_id = %s",
            [first.id, second.id],
        )
        self.env.cr.execute(
            "UPDATE pms_room SET create_date = '2024-01-01 08:00:00' WHERE id = %s",
            [first.id],
        )
        self.env.cr.execute(
            "UPDATE pms_room SET create_date = '2030-01-01 08:00:00' WHERE id = %s",
            [second.id],
        )
        self.env.invalidate_all()
        response = self._kpis(
            date_from="2026-09-22",
            date_to="2026-09-22",
            property_ids=[prop.id],
            compare_last_year=False,
        )
        totals = response["result"]["totals"]
        self.assertEqual(totals["room_nights_sold"], 2)
        self.assertEqual(totals["room_nights_available"], 1)
        self.assertEqual(totals["occupancy_pct"], 200.0)
        self.assertTrue(
            any("above 100%" in text for text in response["warnings"]),
            response["warnings"],
        )

    def test_scope_and_validation(self):
        with self.assertRaises(AccessError):
            self._kpis(property_ids=[self.prop3.id])
        only_prop2 = self._kpis(property_ids=[self.prop2.id])["result"]
        self.assertEqual(only_prop2["totals"]["room_nights_sold"], 2)
        self.assertEqual(len(only_prop2["by_property"]), 1)
        start = date(2026, 1, 1)
        too_long = (start + timedelta(days=93)).isoformat()
        bad_calls = [
            {"date_from": "2026-01-01", "date_to": too_long},
            {"date_from": "2026-09-27", "date_to": "2026-09-21"},
            {"date_from": "2026-09-21", "date_to": "2026-09-27", "domain": "[]"},
            {"date_from": "27/09/2026", "date_to": "2026-09-27"},
            {"date_from": "2026-09-21"},
            {"date_from": "2026-09-21", "date_to": "2026-09-27", "property_ids": "1"},
            {
                "date_from": "2026-09-21",
                "date_to": "2026-09-27",
                "compare_last_year": "yes",
            },
        ]
        for kwargs in bad_calls:
            with self.subTest(kwargs=kwargs), self.assertRaises(UserError):
                self._run("pms_revenue_kpis", **kwargs)
        # 93 days are allowed.
        ok = (start + timedelta(days=92)).isoformat()
        self.assertTrue(
            self._run("pms_revenue_kpis", date_from="2026-01-01", date_to=ok)
        )
