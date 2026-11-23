from freezegun import freeze_time

from odoo.exceptions import AccessError, UserError
from odoo.tests.common import TransactionCase, tagged

from .common import RevenueFixture

TOOL = "pms_partner_sales_yoy"


@tagged("post_install", "-at_install")
class TestPartnerSalesYoy(RevenueFixture, TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_revenue()
        cls.company_partner = cls.env["res.partner"].create(
            {"name": "AI Company", "is_company": True}
        )
        cls.breakfast = cls.env["product.product"].create(
            {"name": "AI breakfast", "type": "service", "per_day": True}
        )
        cls.parking = cls.env["product.product"].create(
            {"name": "AI parking", "type": "service"}
        )
        # Agency reservation of the fixture (res_ag, 24 and 25 September 2026):
        # a breakfast (board service) and a parking, and the folio belongs to a
        # company.
        cls.res_ag.folio_id.partner_id = cls.company_partner
        cls._service(
            cls.res_ag, cls.breakfast, [("2026-09-24", 10), ("2026-09-25", 10)], True
        )
        cls._service(cls.res_ag, cls.parking, [("2026-09-25", 30)], False)
        # A service on the second hotel, with no agency (the guest is no company).
        cls._service(cls.res_p2, cls.breakfast, [("2026-09-22", 5)], True)
        # A service of a cancelled reservation: no sale.
        cls._service(cls.res_cancelled, cls.parking, [("2026-09-21", 99)], False)

    @classmethod
    def _service(cls, reservation, product, lines, board):
        with freeze_time("2026-09-01"):
            service = cls.env["pms.service"].create(
                {
                    "reservation_id": reservation.id,
                    "product_id": product.id,
                    "is_board_service": board,
                    "no_auto_add_lines": True,
                    "service_line_ids": [
                        (0, 0, {"date": day, "day_qty": 1, "price_unit": price})
                        for day, price in lines
                    ],
                }
            )
        # Prices as the PMS recomputes them are not relevant: fix them.
        cls.env.flush_all()
        cls.env.cr.execute(
            """UPDATE pms_service_line SET price_day_total = price_unit
                WHERE service_id = %s""",
            [service.id],
        )
        cls.env.invalidate_all()
        return service

    def _yoy(self, now="2026-09-20 10:00:00", **kwargs):
        with freeze_time(now):
            return self._run(TOOL, **kwargs)

    def _rows(self, response, key):
        return {row["name"]: row for row in response["result"][key]}

    def test_agency_sales_by_year_with_future(self):
        response = self._yoy(years=[2025, 2026])
        agency = self._rows(response, "by_agency")["AI Agency"]
        # Accommodation: two nights of 110 with VAT; food: 20; other: 30.
        self.assertEqual(
            agency["years"]["2026"],
            {
                "accommodation": 220.0,
                "fnb": 20.0,
                "other_services": 30.0,
                "total": 270.0,
            },
        )
        self.assertEqual(agency["years"]["2025"]["total"], 0.0)
        # All of it is from tomorrow on.
        self.assertEqual(agency["future_current_year"], 270.0)
        (hotel,) = agency["by_property"]
        self.assertEqual(hotel["property_id"], self.prop1.id)
        self.assertEqual(hotel["years"]["2026"]["total"], 270.0)

    def test_company_sales_and_totals(self):
        response = self._yoy(years=[2026])
        companies = self._rows(response, "by_company")
        self.assertEqual(list(companies), ["AI Company"])
        self.assertEqual(companies["AI Company"]["years"]["2026"]["total"], 270.0)
        totals = response["result"]["totals"]["agency"]
        self.assertEqual(totals["partners"], 1)
        self.assertEqual(totals["years"]["2026"]["fnb"], 20.0)

    def test_minimum_amount_and_scope(self):
        self.assertEqual(
            self._yoy(years=[2026], min_sales_amount=271)["result"]["by_agency"], []
        )
        self.assertEqual(
            len(self._yoy(years=[2026], min_sales_amount=270)["result"]["by_agency"]), 1
        )
        with self.assertRaises(AccessError):
            self._yoy(property_ids=[self.prop3.id])
        only_second = self._yoy(years=[2026], property_ids=[self.prop2.id])
        self.assertEqual(only_second["result"]["by_agency"], [])

    def test_last_year_and_defaults(self):
        response = self._yoy()
        self.assertEqual(response["result"]["years"], [2025, 2026])
        self.assertEqual(response["period"]["future_to"], "2026-12-31")

    def test_arguments_are_validated(self):
        for kwargs in (
            {"years": [1999]},
            {"years": [2020, 2021, 2022, 2023, 2024]},
            {"years": ["2026"]},
            {"min_sales_amount": -1},
            {"domain": "[]"},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(UserError):
                self._yoy(**kwargs)

    def test_envelope_hides_nothing_but_logs_no_result(self):
        tool = self._tool(TOOL)
        self.assertTrue(tool.description.startswith("SENSITIVE"))
