import datetime

from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.addons.pms.tests.common import TestPms


class DownpaymentCase(TestPms, AccountTestInvoicingCommon):
    """A real folio with a real down payment invoice, shared by every section.

    Building one by hand rather than through the wizard keeps the fixture
    honest about what the code actually keys off: a folio.sale.line flagged
    is_downpayment, linked from the invoice line. That link is what
    account.move._is_downpayment() reads."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        user = cls.env["res.users"].browse(1)
        cls.env = cls.env(user=user)
        cls.company = cls.env.ref("base.main_company")
        # The queue.job.function record is written while this module is being
        # installed, after job_config's ormcache has already been populated in
        # the same registry.
        cls.env.registry.clear_caches()
        cls.anonymous = cls.env.ref("pms.various_pms_partner")
        income = cls.env["account.account"].search(
            [("account_type", "=", "income"), ("company_id", "=", cls.company.id)],
            limit=1,
        )
        cls.sale_journal = cls.env["account.journal"].create(
            {
                "name": "Transfer sale",
                "code": "TRSL",
                "type": "sale",
                "company_id": cls.company.id,
                "default_account_id": income.id,
            }
        )
        # The folio lives in the main company, so the bank journal has to as
        # well: AccountTestInvoicingCommon builds its own separate company.
        cls.bank_journal = cls.env["account.journal"].create(
            {
                "name": "Transfer bank",
                "code": "TRBK",
                "type": "bank",
                "company_id": cls.company.id,
            }
        )
        cls.property = cls.env["pms.property"].create(
            {
                "name": "Transfer PMS TEST",
                "company_id": cls.company.id,
                "default_pricelist_id": cls.pricelist1.id,
            }
        )
        cls.room_type = cls.env["pms.room.type"].create(
            {
                "pms_property_ids": [cls.property.id],
                "name": "Double Transfer",
                "default_code": "DBL_TR",
                "class_id": cls.room_type_class1.id,
                "list_price": 100,
            }
        )
        cls.room = cls.env["pms.room"].create(
            {
                "pms_property_id": cls.property.id,
                "name": "Transfer 101",
                "room_type_id": cls.room_type.id,
                "capacity": 2,
            }
        )
        # A property with no invoicing journals cannot issue a down payment at
        # all: the wizard resolves the journal through the property, not through
        # the folio.
        cls.simplified_journal = cls.env["account.journal"].create(
            {
                "name": "Transfer simplified",
                "code": "TRSI",
                "type": "sale",
                "company_id": cls.company.id,
                "default_account_id": income.id,
            }
        )
        cls.property.write(
            {
                "journal_simplified_invoice_id": cls.simplified_journal.id,
                "journal_normal_invoice_id": cls.sale_journal.id,
                "max_amount_simplified_invoice": 1000.0,
            }
        )
        cls.guest = cls.env["res.partner"].create({"name": "Transfer Guest"})
        cls.sale_channel = cls.env["pms.sale.channel"].create(
            {"name": "Direct Transfer", "channel_type": "direct"}
        )

    def _folio(self):
        today = datetime.date.today()
        reservation = self.env["pms.reservation"].create(
            {
                "pms_property_id": self.property.id,
                "checkin": today - datetime.timedelta(days=3),
                "checkout": today - datetime.timedelta(days=1),
                "adults": 1,
                "room_type_id": self.room_type.id,
                "partner_id": self.guest.id,
                "sale_channel_origin_id": self.sale_channel.id,
            }
        )
        return reservation.folio_id

    def _tax(self, percent=10.0):
        return self.env["account.tax"].create(
            {
                "name": f"Downpayment tax {percent}",
                "amount_type": "percent",
                "amount": percent,
                "type_tax_use": "sale",
                "company_id": self.company.id,
            }
        )

    def _downpayment(self, folio, partner, amount=50.0, taxes=None):
        """Same shape the down payment wizard produces: an invoice tied to the
        folio whose line points at a down payment folio.sale.line. That link is
        what account.move._is_downpayment() keys off."""
        product = self.room_type.product_id
        sale_line = self.env["folio.sale.line"].create(
            {
                "folio_id": folio.id,
                "name": "Down payment",
                "is_downpayment": True,
                "product_id": product.id,
                "product_uom": product.uom_id.id,
                "product_uom_qty": 0,
                "price_unit": amount,
                "default_invoice_to": partner.id,
            }
        )
        invoice = self.env["account.move"].create(
            {
                "move_type": "out_invoice",
                "partner_id": partner.id,
                "journal_id": self.sale_journal.id,
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
                            "tax_ids": [(6, 0, taxes.ids if taxes else [])],
                            "folio_line_ids": [(6, 0, sale_line.ids)],
                        },
                    )
                ],
            }
        )
        invoice.invoice_payment_term_id = False
        invoice.action_post()
        return invoice, sale_line

    def _collect(self, invoice):
        payment = self.env["account.payment"].create(
            {
                "payment_type": "inbound",
                "partner_type": "customer",
                "partner_id": invoice.partner_id.id,
                "amount": invoice.amount_total,
                "date": invoice.date,
                "journal_id": self.bank_journal.id,
            }
        )
        payment.action_post()
        (invoice.line_ids + payment.move_id.line_ids).filtered(
            lambda line: (
                line.account_id.account_type == "asset_receivable"
                and not line.reconciled
            )
        ).reconcile()
        return payment

    def _final_invoice(self, folio, partner, amount=200.0):
        invoice = self.env["account.move"].create(
            {
                "move_type": "out_invoice",
                "partner_id": partner.id,
                "journal_id": self.sale_journal.id,
                "invoice_date": datetime.date.today(),
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
        invoice.invoice_payment_term_id = False
        return invoice
