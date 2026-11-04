from freezegun import freeze_time

from odoo.exceptions import AccessError, UserError
from odoo.tests.common import TransactionCase, tagged

from .common import RevenueFixture


@tagged("post_install", "-at_install")
class TestPickupAndPace(RevenueFixture, TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_revenue()

    def _pickup(self, **kwargs):
        kwargs.setdefault("stay_from", "2026-09-21")
        kwargs.setdefault("stay_to", "2026-09-27")
        kwargs.setdefault("booked_from", "2026-09-14")
        kwargs.setdefault("booked_to", "2026-09-20")
        return self._run("pms_pickup", **kwargs)

    def _pace(self, **kwargs):
        kwargs.setdefault("stay_from", "2026-09-21")
        kwargs.setdefault("stay_to", "2026-09-27")
        with freeze_time("2026-09-20 10:00:00"):
            return self._run("pms_pace_vs_last_year", **kwargs)

    # Pickup

    def test_pickup_totals(self):
        response = self._pickup()
        totals = response["result"]["totals"]
        # Created in the window (Madrid time) and still on the books: the first
        # reservation, the agency one (created at 00:30 of the first day) and
        # the one of the second hotel. The courtesy, created at 00:30 of the
        # day after the window, and the one made before are out.
        self.assertEqual(totals["booked_nights"], 7)
        self.assertEqual(totals["booked_revenue"], 900.0)
        self.assertEqual(totals["booked_adr"], 128.57)
        # Created in the window and cancelled afterwards: one cancelled and one
        # replaced by a modification.
        self.assertEqual(totals["booked_then_cancelled_nights"], 2)
        # Cancelled in the window, whenever booked: those two and one booked
        # before the window (2 nights).
        self.assertEqual(totals["cancelled_nights"], 4)
        self.assertEqual(totals["cancelled_revenue"], 400.0)
        self.assertEqual(totals["cancelled_modified_nights"], 1)
        # Net: booked minus what was booked before and cancelled in the window.
        self.assertEqual(totals["net_nights"], 5)
        self.assertEqual(totals["net_revenue"], 700.0)

    def test_pickup_by_month_and_property(self):
        response = self._pickup()
        months = response["result"]["by_month"]
        self.assertEqual([row["month"] for row in months], ["2026-09"])
        self.assertEqual(months[0]["booked_nights"], 7)
        rows = self._by_property(response)
        self.assertEqual(list(rows), [self.prop1.id, self.prop2.id])
        prop1 = rows[self.prop1.id]
        self.assertEqual(prop1["booked_nights"], 5)
        self.assertEqual(prop1["booked_revenue"], 500.0)
        self.assertEqual(prop1["cancelled_nights"], 4)
        self.assertEqual(prop1["net_nights"], 3)
        self.assertEqual(
            [(m["month"], m["booked_nights"]) for m in prop1["by_month"]],
            [("2026-09", 5)],
        )
        prop2 = rows[self.prop2.id]
        self.assertEqual(prop2["booked_revenue"], 400.0)
        self.assertEqual(prop2["cancelled_nights"], 0)

    def test_pickup_warnings(self):
        warnings = " ".join(self._pickup()["warnings"])
        self.assertIn("1 of the cancelled nights come from modified bookings", warnings)
        self.assertIn(
            "1 nights belong to cancelled reservations with no cancel", warnings
        )
        # The courtesy is outside the window, so there is no zero-value note.
        self.assertNotIn("worth 0", warnings)

    def test_pickup_follows_the_time_zone(self):
        # In UTC the window is a different one: the agency reservation (created
        # at 22:30 the day before) is out and the courtesy (22:30 on the last
        # day) is in.
        self.user.tz = "UTC"
        response = self._pickup()
        totals = response["result"]["totals"]
        self.assertEqual(totals["booked_nights"], 3 + 2 + 2)
        self.assertEqual(totals["booked_revenue"], 700.0)
        self.assertEqual(response["period"]["time_zone"], "UTC")
        self.assertTrue(any("worth 0" in text for text in response["warnings"]))

    def test_pickup_default_window_is_the_last_7_days(self):
        with freeze_time("2026-09-20 12:00:00"):
            response = self._run(
                "pms_pickup", stay_from="2026-09-21", stay_to="2026-09-27"
            )
            only_from = self._run(
                "pms_pickup",
                stay_from="2026-09-21",
                stay_to="2026-09-27",
                booked_from="2026-09-14",
            )
        for answer in (response, only_from):
            self.assertEqual(answer["period"]["booked_from"], "2026-09-14")
            self.assertEqual(answer["period"]["booked_to"], "2026-09-20")
            self.assertEqual(answer["result"]["totals"]["booked_nights"], 7)

    def test_pickup_stay_period_limits_the_nights(self):
        response = self._pickup(stay_from="2026-09-22", stay_to="2026-09-23")
        # Only the nights of the 22nd and 23rd: the first reservation (2), the
        # one of the second hotel (2); the agency one is dated the 24th.
        self.assertEqual(response["result"]["totals"]["booked_nights"], 4)

    def test_pickup_scope_and_validation(self):
        with self.assertRaises(AccessError):
            self._pickup(property_ids=[self.prop3.id])
        bad_calls = [
            {"booked_from": "2026-08-01", "booked_to": "2026-09-01"},
            {"booked_from": "2026-09-20", "booked_to": "2026-09-14"},
            {"stay_from": "2026-01-01", "stay_to": "2026-04-30"},
            {"stay_from": "2026-09-27", "stay_to": "2026-09-21"},
            {"domain": "[]"},
            {"booked_from": "14/09/2026"},
        ]
        for kwargs in bad_calls:
            with self.subTest(kwargs=kwargs), self.assertRaises(UserError):
                self._pickup(**kwargs)
        with self.assertRaises(UserError):
            self._run("pms_pickup", stay_from="2026-09-21")
        # A window of 31 days is allowed.
        self.assertTrue(self._pickup(booked_from="2026-08-21", booked_to="2026-09-20"))

    def test_pickup_envelope(self):
        response = self._pickup()
        self.assertEqual(response["tool"], "pms_pickup")
        self.assertEqual(response["environment"], "test")
        self.assertEqual(response["period"]["stay_from"], "2026-09-21")
        self.assertIn("7 room nights (900.0 without VAT)", response["summary"])
        definitions = " ".join(response["definitions"])
        for text in ("Booked:", "Cancelled:", "Net:", "without VAT"):
            self.assertIn(text, definitions)

    # Pace

    def test_pace_against_last_year_at_the_same_time(self):
        response = self._pace()
        totals = response["result"]["totals"]
        self.assertEqual(
            totals["now"],
            {"room_nights": 11, "room_revenue": 1200.0, "adr": 133.33},
        )
        # Last year, on the books on 20 September: the first reservation, plus
        # one cancelled after that day. Not the one created later, nor the one
        # cancelled before, nor the one cancelled without a date.
        self.assertEqual(
            totals["last_year_same_time"],
            {"room_nights": 3, "room_revenue": 280.0, "adr": 93.33},
        )
        self.assertEqual(
            totals["last_year_final"],
            {"room_nights": 3, "room_revenue": 290.0, "adr": 96.67},
        )
        changes = totals["change_vs_last_year_same_time"]
        self.assertEqual(changes["room_nights"], {"change": 8, "change_pct": 266.7})
        self.assertEqual(
            changes["room_revenue"], {"change": 920.0, "change_pct": 328.6}
        )
        self.assertEqual(changes["adr"], {"change": 40.0, "change_pct": 42.9})
        self.assertIn(
            "On the books 11 room nights (1200.0 without VAT)", response["summary"]
        )
        self.assertIn("last year at the same time 3 (280.0)", response["summary"])
        self.assertIn("266.7%", response["summary"])

    def test_pace_by_property_and_month(self):
        response = self._pace()
        rows = self._by_property(response)
        prop1, prop2 = rows[self.prop1.id], rows[self.prop2.id]
        self.assertEqual(prop1["now"]["room_nights"], 9)
        self.assertEqual(prop1["now"]["room_revenue"], 800.0)
        self.assertEqual(prop1["last_year_same_time"]["room_nights"], 3)
        self.assertEqual(prop1["last_year_final"]["room_revenue"], 290.0)
        self.assertEqual(prop2["now"]["room_revenue"], 400.0)
        self.assertEqual(prop2["last_year_same_time"]["room_nights"], 0)
        self.assertIsNone(prop2["last_year_same_time"]["adr"])
        months = response["result"]["by_month"]
        self.assertEqual([row["month"] for row in months], ["2026-09"])
        self.assertEqual(months[0]["now"]["room_nights"], 11)
        self.assertEqual(months[0]["last_year_same_time"]["room_nights"], 3)

    def test_pace_period_and_definitions(self):
        response = self._pace()
        period = response["period"]
        self.assertEqual(
            period["last_year"], {"date_from": "2025-09-21", "date_to": "2025-09-27"}
        )
        self.assertEqual(period["now"], "2026-09-20T12:00+02:00")
        self.assertEqual(period["last_year_as_of"], "2025-09-20T12:00+02:00")
        definitions = " ".join(response["definitions"])
        for text in ("On the books now:", "Last year at the same time:", "rebuilt"):
            self.assertIn(text, definitions)

    def test_pace_warns_about_undated_cancellations(self):
        warnings = " ".join(self._pace()["warnings"])
        self.assertIn(
            "1 nights of last year belong to cancelled reservations", warnings
        )
        # All the stays are still to come on 20 September.
        self.assertNotIn("already past", warnings)

    def test_pace_warns_about_stays_in_the_past(self):
        with freeze_time("2026-09-25 10:00:00"):
            response = self._run(
                "pms_pace_vs_last_year", stay_from="2026-09-21", stay_to="2026-09-27"
            )
        self.assertTrue(any("already past" in text for text in response["warnings"]))

    def test_pace_scope_and_validation(self):
        with self.assertRaises(AccessError):
            self._pace(property_ids=[self.prop3.id])
        only_prop2 = self._pace(property_ids=[self.prop2.id])["result"]
        self.assertEqual(only_prop2["totals"]["now"]["room_nights"], 2)
        for kwargs in (
            {"stay_from": "2026-01-01", "stay_to": "2026-04-30"},
            {"stay_from": "2026-09-27", "stay_to": "2026-09-21"},
            {"domain": "[]"},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(UserError):
                self._pace(**kwargs)
