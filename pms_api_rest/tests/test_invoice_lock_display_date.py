# Copyright 2026 Commit [Sun]
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
"""``pms_reservation_invoice_lock`` refuses to post a reservation invoice
until a configurable condition is met, but the legacy front-end has no way of
knowing that in advance: it decides whether to offer the manual validation
button from the ``date`` field of the invoice datamodel.

While the lock applies, ``get_invoices`` reports a future date for the draft,
so the button is not offered for something ``_post()`` would refuse anyway.
"""
import datetime

from odoo.tests import tagged

from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.addons.base_rest.controllers.main import _PseudoCollection
from odoo.addons.component.core import WorkContext
from odoo.addons.pms.tests.common import TestPms


@tagged("post_install", "-at_install")
class TestInvoiceLockDisplayDate(TestPms, AccountTestInvoicingCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Use admin (uid=1) so record rules do not get in the way; the API
        # endpoint itself sudo()es its lookups.
        cls.env = cls.env(user=cls.env["res.users"].browse(1))
        cls.company = cls.env.ref("base.main_company")
        cls.simplified_journal = cls.env["account.journal"].create(
            {
                "name": "Simplified journal lock",
                "code": "SMPLK",
                "type": "sale",
                "company_id": cls.company.id,
            }
        )
        cls.property = cls.env["pms.property"].create(
            {
                "name": "Property for invoice-lock tests",
                "company_id": cls.company.id,
                "default_pricelist_id": cls.pricelist1.id,
                "journal_simplified_invoice_id": cls.simplified_journal.id,
            }
        )
        cls.property.user_ids = [(4, cls.env.user.id)]
        cls.room_type = cls.env["pms.room.type"].create(
            {
                "pms_property_ids": [cls.property.id],
                "name": "Double Lock Test",
                "default_code": "DBL_LOCK",
                "class_id": cls.room_type_class1.id,
                "list_price": 25,
            }
        )
        cls.env["pms.room"].create(
            {
                "pms_property_id": cls.property.id,
                "name": "Room 301",
                "room_type_id": cls.room_type.id,
                "capacity": 2,
            }
        )
        cls.partner = cls.env["res.partner"].create(
            {
                "name": "Lock Test Guest",
                "vat": "45224522J",
                "country_id": cls.env.ref("base.es").id,
                "city": "Madrid",
                "zip": "28013",
                "street": "Calle Falsa 1",
            }
        )
        cls.sale_channel = cls.env["pms.sale.channel"].create(
            {"name": "Direct lock test", "channel_type": "direct"}
        )

    def setUp(self):
        super().setUp()
        # Each test sets the policy it needs; never leave it on for the others.
        self.company.reservation_invoice_block_policy = "disabled"

    def _service(self):
        collection = _PseudoCollection("pms.services", self.env)
        work = WorkContext(
            model_name="rest.service.registration", collection=collection
        )
        return work.component(usage="invoices")

    def _draft_invoice_not_departed(self):
        """A draft invoice of a stay that has not checked out yet."""
        today = datetime.date.today()
        reservation = self.env["pms.reservation"].create(
            {
                "pms_property_id": self.property.id,
                "checkin": today,
                "checkout": today + datetime.timedelta(days=2),
                "adults": 2,
                "room_type_id": self.room_type.id,
                "partner_id": self.partner.id,
                "sale_channel_origin_id": self.sale_channel.id,
            }
        )
        moves = reservation.folio_id._create_invoices()
        invoice = moves.filtered(lambda m: m.move_type == "out_invoice")
        self.assertTrue(invoice, "The folio flow did not create an invoice")
        return invoice

    def test_not_locked_when_policy_is_disabled(self):
        invoice = self._draft_invoice_not_departed()
        self.assertFalse(self._service()._is_reservation_invoice_locked(invoice))

    def test_locked_when_stay_has_not_departed(self):
        invoice = self._draft_invoice_not_departed()
        self.company.reservation_invoice_block_policy = "checkout"
        self.assertTrue(self._service()._is_reservation_invoice_locked(invoice))

    def test_locked_invoice_reports_a_future_date(self):
        """The endpoint must report a date the front-end reads as 'not yet'."""
        invoice = self._draft_invoice_not_departed()
        self.company.reservation_invoice_block_policy = "checkout"
        search_param = self.env.datamodels["pms.invoice.search.param"](
            pmsPropertyId=self.property.id,
        )
        results = self._service().get_invoices(search_param)
        reported = next((inv for inv in results.invoices if inv.id == invoice.id), None)
        self.assertTrue(reported, "The draft invoice must be listed")
        self.assertGreater(
            reported.date[:10],
            str(datetime.date.today()),
            "A locked draft must report a future date, not today or the checkout",
        )
