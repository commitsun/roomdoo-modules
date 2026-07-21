import datetime

from odoo.exceptions import UserError
from odoo.tests import tagged

from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.addons.pms.tests.common import TestPms

# Domains evaluated server-side with the helpers of _get_reservation_lock_eval_context.
BLOCK_FUTURE_CHECKOUT = "[('checkout', '>', context_today().strftime('%Y-%m-%d'))]"
BLOCK_PAST_CHECKOUT = "[('checkout', '<', context_today().strftime('%Y-%m-%d'))]"


@tagged("post_install", "-at_install")
class TestReservationInvoiceLock(TestPms, AccountTestInvoicingCommon):
    """Invoices are built through the real folio flow (_create_invoices) so the test
    keeps exercising that path if it ever changes. AccountTestInvoicingCommon provides
    the chart of accounts the flow needs."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        user = cls.env["res.users"].browse(1)
        cls.env = cls.env(user=user)
        cls.company = cls.env.ref("base.main_company")
        cls.simplified_journal = cls.env["account.journal"].create(
            {
                "name": "Simplified journal",
                "code": "SMPL",
                "type": "sale",
                "company_id": cls.company.id,
            }
        )
        cls.property = cls.env["pms.property"].create(
            {
                "name": "Lock PMS TEST",
                "company_id": cls.company.id,
                "default_pricelist_id": cls.pricelist1.id,
                "journal_simplified_invoice_id": cls.simplified_journal.id,
            }
        )
        cls.room_type = cls.env["pms.room.type"].create(
            {
                "pms_property_ids": [cls.property.id],
                "name": "Double Lock",
                "default_code": "DBL_LOCK",
                "class_id": cls.room_type_class1.id,
                "list_price": 25,
            }
        )
        cls.room1 = cls.env["pms.room"].create(
            {
                "pms_property_id": cls.property.id,
                "name": "Lock 101",
                "room_type_id": cls.room_type.id,
                "capacity": 2,
            }
        )
        cls.room2 = cls.env["pms.room"].create(
            {
                "pms_property_id": cls.property.id,
                "name": "Lock 102",
                "room_type_id": cls.room_type.id,
                "capacity": 2,
            }
        )
        cls.partner = cls.env["res.partner"].create(
            {
                "name": "Lock Guest",
                "vat": "45224522J",
                "country_id": cls.env.ref("base.es").id,
                "city": "Madrid",
                "zip": "28013",
                "street": "Calle de la calle",
            }
        )
        cls.sale_channel = cls.env["pms.sale.channel"].create(
            {"name": "Direct Lock", "channel_type": "direct"}
        )
        cls.bypass_group = cls.env.ref(
            "pms_reservation_invoice_lock.group_bypass_reservation_invoice_lock"
        )

    def _create_reservation(self, checkin, checkout, folio=None):
        vals = {
            "pms_property_id": self.property.id,
            "checkin": checkin,
            "checkout": checkout,
            "adults": 1,
            "room_type_id": self.room_type.id,
            "partner_id": self.partner.id,
            "sale_channel_origin_id": self.sale_channel.id,
        }
        if folio:
            vals["folio_id"] = folio.id
        return self.env["pms.reservation"].create(vals)

    def _invoice_for(self, reservation):
        moves = reservation.folio_id._create_invoices()
        invoice = moves.filtered(lambda m: m.move_type == "out_invoice")
        self.assertTrue(invoice, "The folio flow did not create an invoice")
        return invoice

    def test_blocks_future_checkout(self):
        """A regular invoice of a reservation not yet departed cannot be posted."""
        today = datetime.date.today()
        res = self._create_reservation(today, today + datetime.timedelta(days=2))
        invoice = self._invoice_for(res)
        self.property.reservation_invoice_block_domain = BLOCK_FUTURE_CHECKOUT
        # end-to-end: the lock fires from action_post -> _post
        with self.assertRaises(UserError):
            invoice.action_post()

    def test_allows_when_domain_does_not_match(self):
        """A reservation outside the block domain is not blocked by the lock."""
        today = datetime.date.today()
        res = self._create_reservation(today, today + datetime.timedelta(days=2))
        invoice = self._invoice_for(res)
        # future checkout does not match "checkout < today", so it is not blocked
        self.property.reservation_invoice_block_domain = BLOCK_PAST_CHECKOUT
        self.assertTrue(invoice._check_reservation_invoice_lock())

    def test_empty_domain_disables_lock(self):
        """No configured domain means no lock at all."""
        today = datetime.date.today()
        res = self._create_reservation(today, today + datetime.timedelta(days=2))
        invoice = self._invoice_for(res)
        self.property.reservation_invoice_block_domain = ""
        self.assertTrue(invoice._check_reservation_invoice_lock())

    def test_downpayment_is_exempt(self):
        """Down payment invoices are never blocked."""
        today = datetime.date.today()
        res = self._create_reservation(today, today + datetime.timedelta(days=2))
        invoice = self._invoice_for(res)
        invoice.line_ids.folio_line_ids.is_downpayment = True
        self.property.reservation_invoice_block_domain = BLOCK_FUTURE_CHECKOUT
        self.assertTrue(invoice._is_downpayment())
        self.assertTrue(invoice._check_reservation_invoice_lock())

    def test_bypass_group_skips_lock(self):
        """A user in the bypass group can validate blocked invoices."""
        today = datetime.date.today()
        res = self._create_reservation(today, today + datetime.timedelta(days=2))
        invoice = self._invoice_for(res)
        self.property.reservation_invoice_block_domain = BLOCK_FUTURE_CHECKOUT
        self.bypass_group.users = [(4, self.env.uid)]
        self.assertTrue(invoice._check_reservation_invoice_lock())

    def test_error_message_uses_configured_text(self):
        """The hotel's configured message is shown in the error."""
        today = datetime.date.today()
        res = self._create_reservation(today, today + datetime.timedelta(days=2))
        invoice = self._invoice_for(res)
        self.property.reservation_invoice_block_domain = BLOCK_FUTURE_CHECKOUT
        self.property.reservation_invoice_block_message = "Invoice only after checkout"
        with self.assertRaises(UserError) as cm:
            invoice._check_reservation_invoice_lock()
        self.assertIn("Invoice only after checkout", str(cm.exception))

    def test_error_lists_blocking_reservations(self):
        """The error lists the reservations that prevent the validation."""
        today = datetime.date.today()
        res = self._create_reservation(today, today + datetime.timedelta(days=2))
        invoice = self._invoice_for(res)
        self.property.reservation_invoice_block_domain = BLOCK_FUTURE_CHECKOUT
        with self.assertRaises(UserError) as cm:
            invoice._check_reservation_invoice_lock()
        self.assertIn(res.name, str(cm.exception))

    def test_multi_reservation_any_blocked_blocks_invoice(self):
        """When an invoice groups several reservations, one blocked reservation is
        enough to block the whole invoice ("all must satisfy")."""
        today = datetime.date.today()
        res_a = self._create_reservation(today, today + datetime.timedelta(days=1))
        self._create_reservation(
            today,
            today + datetime.timedelta(days=3),
            folio=res_a.folio_id,
        )
        invoice = self._invoice_for(res_a)
        self.assertEqual(len(invoice.line_ids.folio_line_ids.reservation_id), 2)
        self.property.reservation_invoice_block_domain = BLOCK_FUTURE_CHECKOUT
        with self.assertRaises(UserError):
            invoice._check_reservation_invoice_lock()
