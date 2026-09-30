from odoo.tests.common import TransactionCase


class TestInvoiceReportGroups(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env["res.partner"].create({"name": "Test customer"})
        cls.rooms = cls.env["product.category"].create(
            {"name": "Rooms", "invoice_report_sequence": 10}
        )
        cls.bar = cls.env["product.category"].create(
            {
                "name": "Bar",
                "invoice_report_sequence": 30,
                "invoice_report_group_mode": "collapse",
                "invoice_report_label": "Bar consumption",
            }
        )
        cls.room = cls.env["product.product"].create(
            {"name": "Room", "categ_id": cls.rooms.id, "lst_price": 100.0}
        )
        cls.coffee = cls.env["product.product"].create(
            {"name": "Coffee", "categ_id": cls.bar.id, "lst_price": 1.0}
        )
        cls.beer = cls.env["product.product"].create(
            {"name": "Beer", "categ_id": cls.bar.id, "lst_price": 2.0}
        )

    def _invoice(self, products):
        """Build a customer invoice with one line per (product, quantity)."""
        return self.env["account.move"].create(
            {
                "move_type": "out_invoice",
                "partner_id": self.partner.id,
                "invoice_line_ids": [
                    (
                        0,
                        0,
                        {
                            "product_id": product.id,
                            "quantity": quantity,
                            "price_unit": product.lst_price,
                            "tax_ids": [(6, 0, [])],
                        },
                    )
                    for product, quantity in products
                ],
            }
        )

    def test_without_configuration_every_line_is_its_own_group(self):
        """A report gets the plain invoice lines when nothing is configured."""
        self.bar.invoice_report_group_mode = "detail"
        invoice = self._invoice([(self.room, 1), (self.coffee, 1), (self.coffee, 1)])
        groups = invoice._report_line_groups()
        self.assertEqual(len(groups), 3)
        self.assertFalse(any(group["collapse"] for group in groups))
        self.assertEqual([len(group["lines"]) for group in groups], [1, 1, 1])

    def test_collapsed_category_yields_one_group(self):
        """Lines of a collapsed category merge into a single group."""
        invoice = self._invoice(
            [
                (self.room, 1),
                (self.coffee, 4),
                (self.beer, 3),
                (self.coffee, 2),
            ]
        )
        groups = invoice._report_line_groups()
        self.assertEqual(len(groups), 2)
        rooms_group, bar_group = groups
        self.assertFalse(rooms_group["collapse"])
        self.assertTrue(bar_group["collapse"])
        self.assertEqual(len(bar_group["lines"]), 3)
        self.assertEqual(bar_group["quantity"], 9)
        self.assertAlmostEqual(bar_group["price_total"], 12.0)

    def test_detail_adds_up_the_same_product(self):
        """Two lines of the same product become a single breakdown entry."""
        invoice = self._invoice([(self.coffee, 4), (self.beer, 3), (self.coffee, 2)])
        (bar_group,) = invoice._report_line_groups()
        self.assertEqual(len(bar_group["detail"]), 2)
        by_name = {entry["name"]: entry for entry in bar_group["detail"]}
        self.assertEqual(by_name["Coffee"]["quantity"], 6)
        self.assertAlmostEqual(by_name["Coffee"]["price_total"], 6.0)
        self.assertEqual(by_name["Beer"]["quantity"], 3)
        self.assertAlmostEqual(by_name["Beer"]["price_total"], 6.0)

    def test_detail_is_sorted_by_amount(self):
        """The breakdown leads with the products that weigh most."""
        invoice = self._invoice([(self.coffee, 1), (self.beer, 5)])
        (bar_group,) = invoice._report_line_groups()
        self.assertEqual(
            [entry["name"] for entry in bar_group["detail"]],
            ["Beer", "Coffee"],
        )

    def test_groups_follow_the_category_sequence(self):
        """The category sequence decides the order, not the invoice order."""
        invoice = self._invoice([(self.coffee, 1), (self.room, 1)])
        groups = invoice._report_line_groups()
        self.assertEqual(
            [group["category"] for group in groups], [self.rooms, self.bar]
        )

    def test_label_falls_back_to_the_category_name(self):
        """An empty label leaves the category name as the heading."""
        invoice = self._invoice([(self.room, 1), (self.coffee, 1)])
        rooms_group, bar_group = invoice._report_line_groups()
        self.assertEqual(rooms_group["label"], "Rooms")
        self.assertEqual(bar_group["label"], "Bar consumption")

    def test_sections_and_notes_stay_out(self):
        """Only real product lines take part in the grouping."""
        invoice = self._invoice([(self.room, 1)])
        invoice.write(
            {
                "invoice_line_ids": [
                    (
                        0,
                        0,
                        {"display_type": "line_section", "name": "S"},
                    ),
                    (0, 0, {"display_type": "line_note", "name": "Note"}),
                ]
            }
        )
        groups = invoice._report_line_groups()
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0]["lines"]), 1)

    def test_totals_match_the_invoice(self):
        """Nothing is lost or double counted on the way to the report."""
        invoice = self._invoice([(self.room, 1), (self.coffee, 4), (self.beer, 3)])
        groups = invoice._report_line_groups()
        self.assertAlmostEqual(
            sum(group["price_total"] for group in groups),
            sum(invoice.invoice_line_ids.mapped("price_total")),
        )
