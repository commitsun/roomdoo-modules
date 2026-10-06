# Copyright 2026 Commit [Sun]
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
"""Fixture for the tests that edit posted invoices through PATCH
/invoices/p/<id>. Kept apart so that the modules extending that edit can reuse
it."""
import datetime

from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.addons.base_rest.controllers.main import _PseudoCollection
from odoo.addons.component.core import WorkContext
from odoo.addons.pms.tests.common import TestPms


class InvoiceServiceCase(TestPms, AccountTestInvoicingCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(user=cls.env["res.users"].browse(1))
        cls.company = cls.env.ref("base.main_company")
        income = cls.env["account.account"].search(
            [("account_type", "=", "income"), ("company_id", "=", cls.company.id)],
            limit=1,
        )
        cls.normal_journal = cls.env["account.journal"].create(
            {
                "name": "Rectification normal",
                "code": "RCNO",
                "type": "sale",
                "company_id": cls.company.id,
                "default_account_id": income.id,
            }
        )
        cls.simplified_journal = cls.env["account.journal"].create(
            {
                "name": "Rectification simplified",
                "code": "RCSI",
                "type": "sale",
                "company_id": cls.company.id,
                "default_account_id": income.id,
                "is_simplified_invoice": True,
            }
        )
        cls.transfer_journal = cls.env["account.journal"].create(
            {
                "name": "Rectification transfers",
                "code": "RCTR",
                "type": "general",
                "company_id": cls.company.id,
            }
        )
        cls.bank_journal = cls.env["account.journal"].create(
            {
                "name": "Rectification bank",
                "code": "RCBK",
                "type": "bank",
                "company_id": cls.company.id,
            }
        )
        cls.property = cls.env["pms.property"].create(
            {
                "name": "Property for rectification tests",
                "company_id": cls.company.id,
                "default_pricelist_id": cls.pricelist1.id,
                "journal_normal_invoice_id": cls.normal_journal.id,
                "journal_simplified_invoice_id": cls.simplified_journal.id,
            }
        )
        cls.property.user_ids = [(4, cls.env.user.id)]
        cls.room_type = cls.env["pms.room.type"].create(
            {
                "pms_property_ids": [cls.property.id],
                "name": "Double Rectification",
                "default_code": "DBL_RECT",
                "class_id": cls.room_type_class1.id,
                "list_price": 100,
            }
        )
        cls.env["pms.room"].create(
            {
                "pms_property_id": cls.property.id,
                "name": "Room 401",
                "room_type_id": cls.room_type.id,
                "capacity": 2,
            }
        )
        cls.guest = cls.env["res.partner"].create(
            {
                "name": "Rectification Guest",
                "vat": "45224522J",
                "country_id": cls.env.ref("base.es").id,
                "city": "Madrid",
                "zip": "28013",
                "street": "Calle Falsa 1",
            }
        )
        cls.company_customer = cls.env["res.partner"].create(
            {
                "name": "Rectification Company",
                "is_company": True,
                "vat": "B12345674",
                "country_id": cls.env.ref("base.es").id,
                "city": "Madrid",
                "zip": "28013",
                "street": "Calle Falsa 2",
            }
        )
        cls.anonymous = cls.env.ref("pms.various_pms_partner")
        cls.sale_channel = cls.env["pms.sale.channel"].create(
            {"name": "Direct rectification", "channel_type": "direct"}
        )

    def _service(self, usage="invoices"):
        collection = _PseudoCollection("pms.services", self.env)
        work = WorkContext(
            model_name="rest.service.registration", collection=collection
        )
        return work.component(usage=usage)

    def _posted_invoice(self, partner=None):
        """A posted invoice of a two night stay, built by pms the way the app
        builds it: the anonymous customer gets the simplified journal, anyone
        else the normal one."""
        today = datetime.date.today()
        reservation = self.env["pms.reservation"].create(
            {
                "pms_property_id": self.property.id,
                "checkin": today - datetime.timedelta(days=2),
                "checkout": today,
                "adults": 2,
                "room_type_id": self.room_type.id,
                "partner_id": self.guest.id,
                "sale_channel_origin_id": self.sale_channel.id,
            }
        )
        invoice = reservation.folio_id._create_invoices(
            partner_invoice_id=(partner or self.guest).id
        ).filtered(lambda m: m.move_type == "out_invoice")
        invoice.invoice_payment_term_id = False
        invoice.action_post()
        return invoice

    def _pay(self, invoice, ref=False):
        payment = self.env["account.payment"].create(
            {
                "payment_type": "inbound",
                "partner_type": "customer",
                "partner_id": invoice.partner_id.id,
                "amount": invoice.amount_total,
                "date": invoice.date,
                "journal_id": self.bank_journal.id,
                "ref": ref,
                "folio_ids": [(6, 0, invoice.folio_ids.ids)],
                "pms_property_id": self.property.id,
            }
        )
        payment.action_post()
        (invoice.line_ids + payment.move_id.line_ids).filtered(
            lambda line: line.account_id.account_type == "asset_receivable"
            and not line.reconciled
        ).reconcile()
        self.assertEqual(invoice.payment_state, "paid", "fixture not paid")
        return payment

    def _patch(self, invoice, **values):
        info = self.env.datamodels["pms.invoice.info"](**values)
        new_id = self._service().update_invoice(invoice.id, info)
        return self.env["account.move"].browse(new_id)

    def _renamed_lines(self, invoice):
        """The moveLines the edit modal sends on every save: every line, with
        its name, whether it changed or not."""
        line_info = self.env.datamodels["pms.invoice.line.info"]
        return [
            line_info(id=line.id, name="Renamed %s" % line.id, quantity=line.quantity)
            for line in invoice.invoice_line_ids.filtered(
                lambda line: line.display_type == "product"
            )
        ]
