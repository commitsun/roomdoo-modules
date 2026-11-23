from freezegun import freeze_time

from odoo.exceptions import AccessError, UserError
from odoo.tests.common import TransactionCase, tagged

from .common import RevenueFixture

TOOL = "pms_daily_management_report"


@tagged("post_install", "-at_install")
class TestDailyManagementReport(RevenueFixture, TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_revenue()
        Product = cls.env["product.product"]
        cls.breakfast = Product.create(
            {"name": "AI breakfast", "type": "service", "per_day": True}
        )
        cls.dinner = Product.create(
            {"name": "AI dinner", "type": "service", "per_day": True}
        )
        cls.parking = Product.create({"name": "AI parking", "type": "service"})
        # Services of the first reservation of the fixture on 22 September.
        cls._service(cls.res_a, cls.breakfast, 10, True)
        cls._service(cls.res_a, cls.dinner, 20, True)
        cls._service(cls.res_a, cls.parking, 30, False)
        # A service of a cancelled reservation carries no revenue.
        cls._service(cls.res_cancelled, cls.parking, 99, False)

    @classmethod
    def _service(cls, reservation, product, price, board):
        with freeze_time("2026-09-01"):
            service = cls.env["pms.service"].create(
                {
                    "reservation_id": reservation.id,
                    "product_id": product.id,
                    "is_board_service": board,
                    "no_auto_add_lines": True,
                    "service_line_ids": [
                        (
                            0,
                            0,
                            {"date": "2026-09-22", "day_qty": 1, "price_unit": price},
                        )
                    ],
                }
            )
        cls.env.flush_all()
        cls.env.cr.execute(
            """UPDATE pms_service_line
                  SET price_day_total = price_unit, price_day_subtotal = price_unit
                WHERE service_id = %s""",
            [service.id],
        )
        cls.env.invalidate_all()

    def _report(self, now="2026-09-23 10:00:00", **kwargs):
        with freeze_time(now):
            return self._run(TOOL, **kwargs)

    def test_day_column_of_the_first_hotel(self):
        response = self._report()
        self.assertEqual(response["period"]["report_day"], "2026-09-22")
        day = self._by_property(response)[self.prop1.id]["day"]
        self.assertEqual(day["rooms_sold"], 1)
        self.assertEqual(day["rooms_blocked"], 1)
        self.assertEqual(day["occupancy_pct"], 33.3)
        self.assertEqual(day["net_adr"], 100.0)
        self.assertEqual(day["gross_adr"], 110.0)
        self.assertEqual(day["revpar"], 33.33)
        self.assertEqual(day["room_revenue"], 100.0)
        self.assertEqual(day["room_revenue_incl_vat"], 110.0)
        self.assertEqual(day["fnb_revenue"], 30.0)
        self.assertEqual(
            [(s["service"], s["revenue"]) for s in day["fnb_revenue_by_service"]],
            [("AI dinner", 20.0), ("AI breakfast", 10.0)],
        )
        self.assertEqual(day["other_services_revenue"], 30.0)
        self.assertIsNone(day["average_length_of_stay"])
        self.assertEqual(
            (day["cancelled_reservations"], day["confirmed_reservations"]), (0, 0)
        )

    def test_month_and_year_to_date(self):
        response = self._report()
        month = self._by_property(response)[self.prop1.id]["month_to_date"]
        year = self._by_property(response)[self.prop1.id]["year_to_date"]
        # Stays started in the month: only the first reservation counts (the
        # other ones are staff, a meeting room, cancelled or out of service).
        self.assertEqual(month["average_length_of_stay"], 3.0)
        # Cancelled in the month: three, leaving out the one that was modified
        # and the one with no cancel date. Created and confirmed: four.
        self.assertEqual(month["cancelled_reservations"], 3)
        self.assertEqual(month["confirmed_reservations"], 4)
        self.assertEqual(month["fnb_revenue"], 30.0)
        self.assertEqual(year["room_revenue"], month["room_revenue"])
        self.assertEqual(year["rooms_sold"], month["rooms_sold"])
        # The nights of 21 and 22 (the first reservation) plus the courtesy: no.
        self.assertEqual(month["rooms_sold"], 2)

    def test_totals_and_second_hotel(self):
        response = self._report()
        totals = response["result"]["totals"]["day"]
        second = self._by_property(response)[self.prop2.id]["day"]
        self.assertEqual(second["rooms_sold"], 1)
        self.assertEqual(second["average_length_of_stay"], 2.0)
        self.assertEqual(totals["rooms_sold"], 2)
        self.assertEqual(totals["fnb_revenue"], 30.0)
        self.assertEqual(totals["room_revenue"], 300.0)

    def test_report_day_and_scope(self):
        response = self._report(report_day="2026-09-21")
        self.assertEqual(response["period"]["report_day"], "2026-09-21")
        day = self._by_property(response)[self.prop1.id]["day"]
        # The first reservation (110) and the staff night do not count.
        self.assertEqual(day["rooms_sold"], 1)
        self.assertEqual(day["average_length_of_stay"], 3.0)
        self.assertEqual(day["fnb_revenue"], 0.0)
        self.assertEqual(
            list(self._by_property(response)), self.prop1.ids + self.prop2.ids
        )
        with self.assertRaises(AccessError):
            self._report(property_ids=[self.prop3.id])

    def test_arguments_are_validated(self):
        for kwargs in ({"report_day": "22/09/2026"}, {"domain": "[]"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(UserError):
                self._report(**kwargs)
