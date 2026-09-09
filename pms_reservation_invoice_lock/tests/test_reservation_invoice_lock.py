import datetime

from freezegun import freeze_time

from odoo.exceptions import UserError
from odoo.tests import tagged

from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.addons.pms.tests.common import TestPms

# Custom ('other') domain, evaluated with _get_reservation_lock_eval_context helpers.
BLOCK_FUTURE_CHECKOUT = "[('checkout', '>', context_today().strftime('%Y-%m-%d'))]"
BLOCK_PAST_CHECKOUT = "[('checkout', '<', context_today().strftime('%Y-%m-%d'))]"


@tagged("post_install", "-at_install")
class TestReservationInvoiceLock(TestPms, AccountTestInvoicingCommon):
    """Invoices are built through the real folio flow (_create_invoices) so the test
    keeps exercising that path if it ever changes. The lock is configured per company
    (res.company), via a policy selector."""

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

    # --- policy: checkout -------------------------------------------------

    def test_checkout_policy_blocks_not_departed(self):
        """policy=checkout: an invoice of a stay not yet departed cannot be posted."""
        today = datetime.date.today()
        res = self._create_reservation(today, today + datetime.timedelta(days=2))
        invoice = self._invoice_for(res)
        self.company.reservation_invoice_block_policy = "checkout"
        # end-to-end: the lock fires from action_post -> _post
        with self.assertRaises(UserError):
            invoice.action_post()

    def test_checkout_policy_allows_after_checkout(self):
        """policy=checkout: checkout == today is not '> today', so it is allowed."""
        today = datetime.date.today()
        res = self._create_reservation(today - datetime.timedelta(days=2), today)
        invoice = self._invoice_for(res)
        self.company.reservation_invoice_block_policy = "checkout"
        self.assertTrue(invoice._check_reservation_invoice_lock())

    # --- policy: checkin --------------------------------------------------

    def test_checkin_policy_blocks_future_arrival(self):
        """policy=checkin: an invoice of a future arrival cannot be posted."""
        today = datetime.date.today()
        res = self._create_reservation(
            today + datetime.timedelta(days=3), today + datetime.timedelta(days=5)
        )
        invoice = self._invoice_for(res)
        self.company.reservation_invoice_block_policy = "checkin"
        with self.assertRaises(UserError):
            invoice._check_reservation_invoice_lock()

    def test_checkin_policy_allows_current_arrival(self):
        """policy=checkin: checkin == today is not '> today', so it is allowed."""
        today = datetime.date.today()
        res = self._create_reservation(today, today + datetime.timedelta(days=2))
        invoice = self._invoice_for(res)
        self.company.reservation_invoice_block_policy = "checkin"
        self.assertTrue(invoice._check_reservation_invoice_lock())

    # --- policy: other (custom domain) -----------------------------------

    def test_other_policy_uses_custom_domain(self):
        """policy=other: the free domain is evaluated (matching -> blocked)."""
        today = datetime.date.today()
        res = self._create_reservation(today, today + datetime.timedelta(days=2))
        invoice = self._invoice_for(res)
        self.company.reservation_invoice_block_policy = "other"
        self.company.reservation_invoice_block_domain = BLOCK_FUTURE_CHECKOUT
        with self.assertRaises(UserError):
            invoice._check_reservation_invoice_lock()

    def test_other_policy_domain_not_matching_allows(self):
        """policy=other: a domain that no reservation matches does not block."""
        today = datetime.date.today()
        res = self._create_reservation(today, today + datetime.timedelta(days=2))
        invoice = self._invoice_for(res)
        self.company.reservation_invoice_block_policy = "other"
        self.company.reservation_invoice_block_domain = BLOCK_PAST_CHECKOUT
        self.assertTrue(invoice._check_reservation_invoice_lock())

    # --- disabled / exemptions -------------------------------------------

    def test_disabled_policy_disables_lock(self):
        """The 'disabled' policy means no lock at all."""
        today = datetime.date.today()
        res = self._create_reservation(today, today + datetime.timedelta(days=2))
        invoice = self._invoice_for(res)
        self.company.reservation_invoice_block_policy = "disabled"
        self.assertTrue(invoice._check_reservation_invoice_lock())

    def test_downpayment_is_exempt(self):
        """Down payment invoices are never blocked."""
        today = datetime.date.today()
        res = self._create_reservation(today, today + datetime.timedelta(days=2))
        invoice = self._invoice_for(res)
        invoice.line_ids.folio_line_ids.is_downpayment = True
        self.company.reservation_invoice_block_policy = "checkout"
        self.assertTrue(invoice._is_downpayment())
        self.assertTrue(invoice._check_reservation_invoice_lock())

    def test_bypass_group_skips_lock(self):
        """A user in the bypass group can validate blocked invoices."""
        today = datetime.date.today()
        res = self._create_reservation(today, today + datetime.timedelta(days=2))
        invoice = self._invoice_for(res)
        self.company.reservation_invoice_block_policy = "checkout"
        self.bypass_group.users = [(4, self.env.uid)]
        self.assertTrue(invoice._check_reservation_invoice_lock())

    # --- multi-reservation & error message -------------------------------

    def test_multi_reservation_any_blocked_blocks_invoice(self):
        """One blocked reservation is enough to block the whole invoice."""
        today = datetime.date.today()
        res_a = self._create_reservation(today, today + datetime.timedelta(days=1))
        self._create_reservation(
            today,
            today + datetime.timedelta(days=3),
            folio=res_a.folio_id,
        )
        invoice = self._invoice_for(res_a)
        self.assertEqual(len(invoice.line_ids.folio_line_ids.reservation_id), 2)
        self.company.reservation_invoice_block_policy = "checkout"
        with self.assertRaises(UserError):
            invoice._check_reservation_invoice_lock()

    def test_error_message_uses_configured_text(self):
        """The company's configured message is shown in the error."""
        today = datetime.date.today()
        res = self._create_reservation(today, today + datetime.timedelta(days=2))
        invoice = self._invoice_for(res)
        self.company.reservation_invoice_block_policy = "checkout"
        self.company.reservation_invoice_block_message = "Invoice only after checkout"
        with self.assertRaises(UserError) as cm:
            invoice._check_reservation_invoice_lock()
        self.assertIn("Invoice only after checkout", str(cm.exception))

    def test_error_lists_blocking_reservations(self):
        """The error lists the reservation codes that prevent the validation."""
        today = datetime.date.today()
        res = self._create_reservation(today, today + datetime.timedelta(days=2))
        invoice = self._invoice_for(res)
        self.company.reservation_invoice_block_policy = "checkout"
        with self.assertRaises(UserError) as cm:
            invoice._check_reservation_invoice_lock()
        self.assertIn(res.name, str(cm.exception))

    # --- cancelled reservations & cancellation penalties -----------------

    def _cancellation_rule(self):
        """A rule that penalises a cancellation made within 30 days of arrival."""
        # No pms_property_ids on purpose: pricelist1 is not restricted to any
        # property either, and multi_pms_properties refuses to link a record
        # scoped to one property to a record that applies to all of them.
        return self.env["pms.cancelation.rule"].create(
            {
                "name": "Lock test rule",
                "days_intime": 30,
                "penalty_late": 100,
                "apply_on_late": "all",
                "penalty_noshow": 100,
                "apply_on_noshow": "all",
            }
        )

    def test_cancelled_reservation_does_not_block(self):
        """A cancelled stay never blocks: it is not going to happen, so there is
        nothing left to wait for."""
        today = datetime.date.today()
        res = self._create_reservation(
            today + datetime.timedelta(days=20), today + datetime.timedelta(days=22)
        )
        invoice = self._invoice_for(res)
        self.company.reservation_invoice_block_policy = "checkout"
        # Sanity: while it is a live future stay, it does block.
        with self.assertRaises(UserError):
            invoice._check_reservation_invoice_lock()
        res.action_cancel()
        self.assertEqual(res.state, "cancel")
        self.assertTrue(invoice._check_reservation_invoice_lock())

    def test_cancelled_reservation_does_not_block_with_checkin_policy(self):
        """Same for policy=checkin: a cancelled arrival is never going to arrive."""
        today = datetime.date.today()
        res = self._create_reservation(
            today + datetime.timedelta(days=20), today + datetime.timedelta(days=22)
        )
        invoice = self._invoice_for(res)
        self.company.reservation_invoice_block_policy = "checkin"
        with self.assertRaises(UserError):
            invoice._check_reservation_invoice_lock()
        res.action_cancel()
        self.assertTrue(invoice._check_reservation_invoice_lock())

    def test_cancellation_penalty_can_be_invoiced(self):
        """The business case: a stay cancelled in advance generates a penalty that
        must be invoiceable straight away, not when the cancelled checkout would
        have been. Goes all the way through action_post()."""
        today = datetime.date.today()
        res = self._create_reservation(
            today + datetime.timedelta(days=5), today + datetime.timedelta(days=7)
        )
        # The rule has to hang from the pricelist the reservation actually uses,
        # which is not necessarily the property default.
        res.pricelist_id.cancelation_rule_id = self._cancellation_rule()
        # The penalty amount is a percentage of the nightly prices, and this
        # test harness leaves them at 0 (the inherited pricelist has no item for
        # the room type), which would silently skip the penalty altogether.
        res.reservation_line_ids.write({"price": 50})
        res.action_cancel()
        self.assertEqual(
            res.cancelled_reason, "late", "The cancellation rule did not apply"
        )
        penalty = res.service_ids.filtered(lambda s: s.is_cancel_penalty)
        self.assertTrue(penalty, "No cancellation penalty service was generated")
        self.company.reservation_invoice_block_policy = "checkout"
        invoice = self._invoice_for(res)
        invoice.action_post()
        self.assertEqual(invoice.state, "posted")

    # --- timezone: the hotel's clock, never the user's -------------------

    @freeze_time("2026-09-09 23:30:00")
    def test_checkout_policy_uses_property_timezone(self):
        """At 23:30 UTC it is already the 10th in Madrid, so a departure on the
        10th is today for the hotel and must not be blocked -- even though the
        user's session, in UTC, still reads the 9th."""
        self.env.user.tz = "UTC"
        self.property.tz = "Europe/Madrid"
        res = self._create_reservation(
            datetime.date(2026, 9, 8), datetime.date(2026, 9, 10)
        )
        invoice = self._invoice_for(res)
        self.company.reservation_invoice_block_policy = "checkout"
        self.assertTrue(
            invoice._check_reservation_invoice_lock(),
            "A departure on the hotel's own today must not be blocked",
        )

    @freeze_time("2026-09-09 23:30:00")
    def test_property_timezone_is_the_one_that_decides(self):
        """Control for the test above: the very same invoice, with the property in
        UTC, IS blocked. Proves the property timezone is what changes the verdict,
        and that the first test is not passing by accident."""
        self.env.user.tz = "Europe/Madrid"
        self.property.tz = "UTC"
        res = self._create_reservation(
            datetime.date(2026, 9, 8), datetime.date(2026, 9, 10)
        )
        invoice = self._invoice_for(res)
        self.company.reservation_invoice_block_policy = "checkout"
        with self.assertRaises(UserError):
            invoice._check_reservation_invoice_lock()
