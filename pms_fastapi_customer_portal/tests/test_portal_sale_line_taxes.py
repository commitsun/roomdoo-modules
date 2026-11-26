from .common import CommonTestCustomerPortalApi


class TestPortalSaleLineTaxes(CommonTestCustomerPortalApi):
    """Sale lines add up to the total, taxes included, to the cent."""

    def setUp(self):
        super().setUp()
        spain = self.env.ref("base.es")
        self.vat_included = self.env["account.tax"].create(
            {
                "name": "VAT 10% included",
                "amount": 10.0,
                "price_include": True,
                "company_id": self.company.id,
                "country_id": spain.id,
            }
        )
        self.vat_excluded = self.env["account.tax"].create(
            {
                "name": "VAT 21% excluded",
                "amount": 21.0,
                "company_id": self.company.id,
                "country_id": spain.id,
            }
        )
        self.double.taxes_id = [(6, 0, self.vat_included.ids)]
        self.parking = self.env["product.product"].create(
            {
                "name": "Parking",
                "list_price": 13.33,
                "per_day": False,
                "taxes_id": [(6, 0, self.vat_excluded.ids)],
            }
        )

    def _folio_with_taxes(self):
        folio = self._create_folio(
            [(self.double, 3, 2, 2, 0), (self.double, 3, 2, 2, 0)]
        )
        folio.reservation_ids.reservation_line_ids.write({"price": 33.33})
        for reservation in folio.reservation_ids:
            self.env["pms.service"].create(
                {"reservation_id": reservation.id, "product_id": self.parking.id}
            )
        folio.reservation_ids[0].reservation_line_ids[:1].write({"discount": 7.0})
        self.assertTrue(folio.sale_line_ids.tax_ids, "The lines should carry taxes")
        return folio

    def _assert_lines_add_up(self, folio):
        body = self._get(f"/portal/{folio.access_token}/folio").json()
        total = round(sum(line["amount"] for line in body["saleLines"]), 2)
        self.assertEqual(total, body["totalAmount"], body["saleLines"])
        self.assertEqual(body["totalAmount"], round(folio.amount_total, 2))
        return body

    def test_lines_add_up_with_taxes_rounded_per_line(self):
        self.company.tax_calculation_rounding_method = "round_per_line"
        folio = self._folio_with_taxes()

        body = self._assert_lines_add_up(folio)

        self.assertEqual(
            body["saleLines"],
            [
                {"name": "Double Portal", "amount": 133.32},
                {"name": "Parking", "amount": 32.26},
                {"name": "Discounts", "amount": -2.33},
            ],
        )

    def test_lines_add_up_with_taxes_rounded_globally(self):
        self.company.tax_calculation_rounding_method = "round_globally"
        folio = self._folio_with_taxes()

        self._assert_lines_add_up(folio)
