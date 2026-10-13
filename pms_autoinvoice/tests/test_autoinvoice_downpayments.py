import datetime

from odoo.tests import tagged

from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.addons.pms.tests.common import TestPms


@tagged("post_install", "-at_install")
class TestAutoinvoiceDownpayments(TestPms, AccountTestInvoicingCommon):
    """How the automatic invoice deducts the down payments of a folio."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(user=cls.env["res.users"].browse(1))
        cls.company = cls.env.ref("base.main_company")
        cls.anonymous = cls.env.ref("pms.various_pms_partner")
        income = cls.env["account.account"].search(
            [("account_type", "=", "income"), ("company_id", "=", cls.company.id)],
            limit=1,
        )
        cls.normal_journal = cls.env["account.journal"].create(
            {
                "name": "Autoinvoice normal",
                "code": "AINV",
                "type": "sale",
                "company_id": cls.company.id,
                "default_account_id": income.id,
            }
        )
        cls.simplified_journal = cls.env["account.journal"].create(
            {
                "name": "Autoinvoice simplified",
                "code": "AISI",
                "type": "sale",
                "company_id": cls.company.id,
                "default_account_id": income.id,
                "is_simplified_invoice": True,
            }
        )
        cls.property = cls.env["pms.property"].create(
            {
                "name": "Autoinvoice down payments",
                "company_id": cls.company.id,
                "default_pricelist_id": cls.pricelist1.id,
                "journal_normal_invoice_id": cls.normal_journal.id,
                "journal_simplified_invoice_id": cls.simplified_journal.id,
                "max_amount_simplified_invoice": 1000.0,
                "default_invoicing_policy": "checkout",
                "margin_days_autoinvoice": 0,
            }
        )
        cls.room_type = cls.env["pms.room.type"].create(
            {
                "pms_property_ids": [cls.property.id],
                "name": "Double Autoinvoice",
                "default_code": "DBL_AI",
                "class_id": cls.room_type_class1.id,
                "list_price": 100,
            }
        )
        cls.env["pms.room"].create(
            {
                "pms_property_id": cls.property.id,
                "name": "Autoinvoice 101",
                "room_type_id": cls.room_type.id,
                "capacity": 2,
            }
        )
        cls.sale_channel = cls.env["pms.sale.channel"].create(
            {"name": "Direct Autoinvoice", "channel_type": "direct"}
        )

    def _folio(self):
        """A stay that checks out today, billed to the anonymous customer like
        the walk-in guests whose down payments were issued as simplified
        invoices."""
        today = datetime.date.today()
        reservation = self.env["pms.reservation"].create(
            {
                "pms_property_id": self.property.id,
                "checkin": today - datetime.timedelta(days=2),
                "checkout": today,
                "adults": 1,
                "room_type_id": self.room_type.id,
                "partner_id": self.anonymous.id,
                "sale_channel_origin_id": self.sale_channel.id,
            }
        )
        reservation.reservation_line_ids.default_invoice_to = self.anonymous
        return reservation.folio_id

    def _downpayment(self, folio, amount):
        """The shape the down payment wizard produces: a posted invoice whose
        line points at a down payment folio.sale.line, taxed like its product
        (pms builds the deduction line from the product taxes too)."""
        product = self.room_type.product_id
        taxes = product.taxes_id.filtered(lambda t: t.company_id == self.company)
        sale_line = self.env["folio.sale.line"].create(
            {
                "folio_id": folio.id,
                "name": "Down payment",
                "is_downpayment": True,
                "product_id": product.id,
                "product_uom": product.uom_id.id,
                "product_uom_qty": 0,
                "price_unit": amount,
                "default_invoice_to": self.anonymous.id,
            }
        )
        invoice = self.env["account.move"].create(
            {
                "move_type": "out_invoice",
                "partner_id": self.anonymous.id,
                "journal_id": self.simplified_journal.id,
                "invoice_date": datetime.date.today(),
                "folio_ids": [(6, 0, folio.ids)],
                "pms_property_id": self.property.id,
                "invoice_line_ids": [
                    (
                        0,
                        0,
                        {
                            "name": "Down payment",
                            "product_id": product.id,
                            "quantity": 1,
                            "price_unit": amount,
                            "tax_ids": [(6, 0, taxes.ids)],
                            "folio_line_ids": [(6, 0, sale_line.ids)],
                        },
                    )
                ],
            }
        )
        invoice.action_post()
        return invoice, sale_line

    def _draft(self, folio, amount):
        """A draft invoice of the folio for the anonymous customer, as the
        automatic invoice leaves them before deducting down payments."""
        return self.env["account.move"].create(
            {
                "move_type": "out_invoice",
                "partner_id": self.anonymous.id,
                "journal_id": self.simplified_journal.id,
                "folio_ids": [(6, 0, folio.ids)],
                "pms_property_id": self.property.id,
                "invoice_line_ids": [
                    (
                        0,
                        0,
                        {
                            "name": "Stay",
                            "quantity": 1,
                            "price_unit": amount,
                            "tax_ids": [(6, 0, [])],
                        },
                    )
                ],
            }
        )

    def _automatic_invoices(self, folio, downpayment):
        return self.env["account.move"].search(
            [
                ("folio_ids", "in", folio.ids),
                ("move_type", "=", "out_invoice"),
                ("state", "=", "posted"),
                ("id", "!=", downpayment.id),
            ]
        )

    @staticmethod
    def _deductions(invoices, sale_line):
        return invoices.invoice_line_ids.filtered(
            lambda line: sale_line in line.folio_line_ids
        )

    def test_downpayment_is_deducted_from_the_automatic_invoice(self):
        """The deduction used to raise AttributeError ('NoneType' object has no
        attribute 'map_tax'): pms maps the taxes of the down payment line
        through the invoice fiscal position, and the call did not pass it."""
        folio = self._folio()
        downpayment, sale_line = self._downpayment(folio, 50.0)
        stay = folio.amount_total

        self.property.autoinvoice_folio(folio)

        invoice = self._automatic_invoices(folio, downpayment)
        self.assertEqual(len(invoice), 1)
        self.assertEqual(len(self._deductions(invoice, sale_line)), 1)
        self.assertAlmostEqual(invoice.amount_total, stay - downpayment.amount_total)
        self.assertEqual(sale_line.qty_invoiced, 0)

    def test_downpayment_larger_than_the_invoice_is_not_deducted(self):
        """Deducting it would leave the invoice negative and unpostable: the
        invoice bills its lines and the down payment is left for review."""
        folio = self._folio()
        _downpayment, sale_line = self._downpayment(folio, 5000.0)
        stay = folio.amount_total

        self.property.autoinvoice_folio(folio)

        invoice = self._automatic_invoices(folio, _downpayment)
        self.assertEqual(len(invoice), 1)
        self.assertFalse(self._deductions(invoice, sale_line))
        self.assertAlmostEqual(invoice.amount_total, stay)
        self.assertTrue(
            any(_downpayment.name in (msg.body or "") for msg in folio.message_ids),
            "The folio must say which down payment was left for review",
        )

    def test_downpayment_is_deducted_once_from_the_largest_invoice(self):
        """A folio split in two invoices for the same customer used to get the
        down payment added to both: deducted twice, or "Expected singleton"."""
        folio = self._folio()
        downpayment, sale_line = self._downpayment(folio, 100.0)
        large = self._draft(folio, 300.0)
        small = self._draft(folio, 50.0)

        not_fitting = self.property._autoinvoice_deduct_downpayments(
            large | small, sale_line, self.env["res.partner"]
        )

        self.assertFalse(not_fitting)
        self.assertEqual(len(self._deductions(large, sale_line)), 1)
        self.assertFalse(self._deductions(small, sale_line))
        self.assertAlmostEqual(large.amount_total, 300.0 - downpayment.amount_total)

    def test_downpayment_that_fits_nowhere_is_returned(self):
        folio = self._folio()
        _downpayment, sale_line = self._downpayment(folio, 1000.0)
        large = self._draft(folio, 300.0)
        small = self._draft(folio, 50.0)

        not_fitting = self.property._autoinvoice_deduct_downpayments(
            large | small, sale_line, self.env["res.partner"]
        )

        self.assertEqual(not_fitting, sale_line)
        self.assertFalse(self._deductions(large | small, sale_line))
        self.assertAlmostEqual(large.amount_total, 300.0)
        self.assertAlmostEqual(small.amount_total, 50.0)

    def test_downpayment_is_not_deducted_when_its_customer_was_moved(self):
        """When the simplified invoice went over the limit and was moved to a
        named host, the host's invoice bills the stay the down payment paid
        for. Deducting it from what is left for the anonymous customer is what
        left negative drafts and down payments refunded twice."""
        folio = self._folio()
        _downpayment, sale_line = self._downpayment(folio, 100.0)
        leftover = self._draft(folio, 300.0)

        not_fitting = self.property._autoinvoice_deduct_downpayments(
            leftover, sale_line, self.anonymous
        )

        self.assertFalse(not_fitting)
        self.assertFalse(self._deductions(leftover, sale_line))
