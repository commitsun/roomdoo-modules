import datetime
from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests import tagged

from odoo.addons.account.tests.common import AccountTestInvoicingCommon

LOCKED_DATE = datetime.date(2023, 1, 15)
LOCK_DATE = datetime.date(2023, 1, 31)
OPEN_DATE = datetime.date(2023, 3, 10)
LATER_OPEN_DATE = datetime.date(2023, 4, 12)


@tagged("post_install", "-at_install")
class TestAccountReconcileLockDate(AccountTestInvoicingCommon):
    """The guard sits on account.partial.reconcile.unlink(), the single
    bottleneck every way of undoing a reconciliation goes through. The fixture
    is always an invoice matched against a payment, each one on its own date,
    because the case the module exists for is precisely an entry in a closed
    month matched against one in an open month."""

    @classmethod
    def setUpClass(cls, chart_template_ref=None):
        super().setUpClass(chart_template_ref=chart_template_ref)
        cls.company = cls.company_data["company"]
        cls.bypass_group = cls.env.ref(
            "account_reconcile_lock_date.group_bypass_reconcile_lock_date"
        )

    def _reconciled_pair(self, invoice_date=LOCKED_DATE, payment_date=OPEN_DATE):
        invoice = self.init_invoice(
            "out_invoice",
            invoice_date=invoice_date,
            post=True,
            amounts=[100.0],
            taxes=[],
        )
        payment = self.env["account.payment"].create(
            {
                "payment_type": "inbound",
                "partner_type": "customer",
                "partner_id": invoice.partner_id.id,
                "amount": 100.0,
                "date": payment_date,
                "journal_id": self.company_data["default_journal_bank"].id,
            }
        )
        payment.action_post()
        lines = (invoice.line_ids + payment.move_id.line_ids).filtered(
            lambda line: line.account_id.account_type == "asset_receivable"
            and not line.reconciled
        )
        lines.reconcile()
        self.assertTrue(invoice.line_ids.matched_credit_ids, "fixture not reconciled")
        return invoice, payment

    def _lock(self, scope="all", lock_date=LOCK_DATE):
        self.company.reconcile_lock_scope = scope
        self.company.period_lock_date = lock_date

    # ------------------------------------------------------------------
    # default state
    # ------------------------------------------------------------------
    def test_scope_is_disabled_on_install(self):
        """Installing the module changes nothing until somebody opts in."""
        self.assertEqual(self.company.reconcile_lock_scope, "disabled")

    def test_disabled_scope_lets_a_locked_reconciliation_be_broken(self):
        """A lock date alone must not block anything."""
        invoice, _payment = self._reconciled_pair()
        self._lock(scope="disabled")
        invoice.line_ids.remove_move_reconcile()
        self.assertFalse(invoice.line_ids.matched_credit_ids)

    # ------------------------------------------------------------------
    # the guard itself
    # ------------------------------------------------------------------
    def test_blocks_when_the_invoice_side_is_locked(self):
        """The real case: invoice in a closed month, payment in an open one."""
        invoice, _payment = self._reconciled_pair()
        self._lock()
        with self.assertRaises(UserError):
            invoice.line_ids.remove_move_reconcile()
        self.assertTrue(invoice.line_ids.matched_credit_ids)

    def test_blocks_when_the_payment_side_is_locked(self):
        """The mirror case: only the payment falls in the closed period."""
        invoice, _payment = self._reconciled_pair(
            invoice_date=OPEN_DATE, payment_date=LOCKED_DATE
        )
        self._lock()
        with self.assertRaises(UserError):
            invoice.line_ids.remove_move_reconcile()

    def test_allows_when_both_sides_are_open(self):
        """Nothing locked, nothing blocked."""
        invoice, _payment = self._reconciled_pair(
            invoice_date=OPEN_DATE, payment_date=LATER_OPEN_DATE
        )
        self._lock()
        invoice.line_ids.remove_move_reconcile()
        self.assertFalse(invoice.line_ids.matched_credit_ids)

    def test_empty_recordset_is_free(self):
        """remove_move_reconcile() runs on every button_draft and every
        _reverse_moves(cancel=True), reconciled or not. The common case is an
        empty recordset and it must never raise."""
        invoice = self.init_invoice(
            "out_invoice", invoice_date=LOCKED_DATE, post=True, amounts=[100.0]
        )
        self._lock()
        invoice.line_ids.remove_move_reconcile()

    def test_error_names_the_blocking_entry(self):
        """Reception has to know which entry to look at."""
        invoice, _payment = self._reconciled_pair()
        self._lock()
        with self.assertRaises(UserError) as error:
            invoice.line_ids.remove_move_reconcile()
        self.assertIn(invoice.name, str(error.exception))

    # ------------------------------------------------------------------
    # which lock date
    # ------------------------------------------------------------------
    def test_period_lock_date_is_honoured(self):
        self.company.fiscalyear_lock_date = False
        self.company.period_lock_date = LOCK_DATE
        self.assertEqual(self.company._get_reconcile_lock_date(), LOCK_DATE)

    def test_the_latest_of_both_lock_dates_wins(self):
        self.company.fiscalyear_lock_date = datetime.date(2022, 12, 31)
        self.company.period_lock_date = LOCK_DATE
        self.assertEqual(self.company._get_reconcile_lock_date(), LOCK_DATE)

    def test_account_manager_is_not_exempt(self):
        """The core's _get_user_fiscal_lock_date() drops period_lock_date for
        advisers. This module must not, or the month-end close would be
        invisible to the very people doing the closing."""
        invoice, _payment = self._reconciled_pair()
        self.env.user.groups_id = [
            (4, self.env.ref("account.group_account_manager").id)
        ]
        self.company.fiscalyear_lock_date = False
        self._lock()
        self.assertEqual(
            self.company._get_user_fiscal_lock_date(),
            datetime.date.min,
            "precondition: the core exempts this user",
        )
        with self.assertRaises(UserError):
            invoice.line_ids.remove_move_reconcile()

    # ------------------------------------------------------------------
    # scope
    # ------------------------------------------------------------------
    def test_downpayment_scope_ignores_a_regular_invoice(self):
        invoice, _payment = self._reconciled_pair()
        self._lock(scope="downpayment")
        invoice.line_ids.remove_move_reconcile()
        self.assertFalse(invoice.line_ids.matched_credit_ids)

    def test_downpayment_scope_blocks_a_down_payment_invoice(self):
        """_is_downpayment() is an account hook that only sale and pms make
        true, so it is patched here on the registry class: patching account's
        own class would be shadowed whenever pms is installed, which is exactly
        what happens in this repository's CI."""
        invoice, _payment = self._reconciled_pair()
        self._lock(scope="downpayment")
        with patch.object(
            type(self.env["account.move"]),
            "_is_downpayment",
            lambda move: move.move_type == "out_invoice",
        ):
            with self.assertRaises(UserError):
                invoice.line_ids.remove_move_reconcile()

    def test_scope_fires_when_either_side_is_in_scope(self):
        """The counterpart of a down payment invoice is a payment, which is
        never one itself: requiring both sides would mean never firing."""
        invoice, payment = self._reconciled_pair()
        self._lock(scope="downpayment")
        with patch.object(
            type(self.env["account.move"]),
            "_is_downpayment",
            lambda move: move == invoice,
        ):
            self.assertFalse(payment.move_id._is_reconcile_lock_in_scope())
            with self.assertRaises(UserError):
                invoice.line_ids.remove_move_reconcile()

    # ------------------------------------------------------------------
    # bypasses
    # ------------------------------------------------------------------
    def test_bypass_group_lifts_the_guard(self):
        invoice, _payment = self._reconciled_pair()
        self._lock()
        self.bypass_group.users = [(4, self.env.uid)]
        invoice.line_ids.remove_move_reconcile()
        self.assertFalse(invoice.line_ids.matched_credit_ids)

    def test_bypass_context_lifts_the_guard(self):
        invoice, _payment = self._reconciled_pair()
        self._lock()
        invoice.line_ids.with_context(
            bypass_reconcile_lock_date=True
        ).remove_move_reconcile()
        self.assertFalse(invoice.line_ids.matched_credit_ids)

    def test_sudo_does_not_lift_the_guard(self):
        """Group membership is checked against the acting user, so elevating
        privileges is not a way out. Both PMS APIs run under sudo()."""
        invoice, _payment = self._reconciled_pair()
        self._lock()
        with self.assertRaises(UserError):
            invoice.sudo().line_ids.remove_move_reconcile()

    # ------------------------------------------------------------------
    # real entry points
    # ------------------------------------------------------------------
    def test_payment_reset_to_draft_is_guarded(self):
        """button_draft() unreconciles before writing the state, so resetting a
        payment is a destructive path and dies in the same guard."""
        invoice, payment = self._reconciled_pair()
        self._lock()
        with self.assertRaises(UserError):
            payment.action_draft()

    def test_unreconcile_widget_button_is_guarded(self):
        """js_remove_outstanding_partial() is the only core path that calls
        partial.unlink() directly, without going through
        remove_move_reconcile(). A guard placed on the latter would miss it."""
        invoice, _payment = self._reconciled_pair()
        self._lock()
        partial = invoice.line_ids.matched_credit_ids[0]
        with self.assertRaises(UserError):
            invoice.js_remove_outstanding_partial(partial.id)

    def test_reversal_with_cancel_is_guarded(self):
        """_reverse_moves(cancel=True) unreconciles the original entry before
        copying it. This is the path pms_autoinvoice uses on down payments."""
        invoice, _payment = self._reconciled_pair()
        self._lock()
        with self.assertRaises(UserError):
            invoice._reverse_moves(cancel=True)

    # ------------------------------------------------------------------
    # the core's own recursion
    # ------------------------------------------------------------------
    def _setup_cash_basis(self):
        """A cash basis tax, so that reconciling generates a tax cash basis
        entry that the core will want to reverse from inside unlink()."""
        self.company.tax_exigibility = True
        self.company.tax_cash_basis_journal_id = self.env["account.journal"].search(
            [("type", "=", "general"), ("company_id", "=", self.company.id)], limit=1
        )
        transition = self.env["account.account"].create(
            {
                "name": "Cash basis transition",
                "code": "CBTRANS",
                "account_type": "liability_current",
                "company_id": self.company.id,
                "reconcile": True,
            }
        )
        return self.env["account.tax"].create(
            {
                "name": "Cash basis 10%",
                "amount": 10.0,
                "amount_type": "percent",
                "type_tax_use": "sale",
                "tax_exigibility": "on_payment",
                "cash_basis_transition_account_id": transition.id,
                "company_id": self.company.id,
            }
        )

    def test_core_cleanup_of_its_own_entries_is_not_self_blocked(self):
        """The core reverses the cash basis entries it owns from *inside*
        unlink(), which loops straight back into this guard. The scope is
        narrowed here to the cash basis entry alone, so the outer unlink is
        authorised while the inner one would not be: if the authorisation did
        not propagate, this deadlocks -- and the cash basis entry cannot be
        reset to draft by hand either, so the state would be unrecoverable."""
        tax = self._setup_cash_basis()
        invoice = self.init_invoice(
            "out_invoice",
            invoice_date=LOCKED_DATE,
            post=True,
            amounts=[100.0],
            taxes=[tax],
        )
        payment = self.env["account.payment"].create(
            {
                "payment_type": "inbound",
                "partner_type": "customer",
                "partner_id": invoice.partner_id.id,
                "amount": 110.0,
                "date": LOCKED_DATE,
                "journal_id": self.company_data["default_journal_bank"].id,
            }
        )
        payment.action_post()
        (invoice.line_ids + payment.move_id.line_ids).filtered(
            lambda line: line.account_id.account_type == "asset_receivable"
            and not line.reconciled
        ).reconcile()
        cash_basis = self.env["account.move"].search(
            [
                ("tax_cash_basis_rec_id", "!=", False),
                ("company_id", "=", self.company.id),
            ]
        )
        self.assertTrue(cash_basis, "precondition: no cash basis entry was created")
        self.assertTrue(
            cash_basis.line_ids.filtered("reconciled"),
            "precondition: the cash basis entry is not reconciled, so unlink() "
            "would never recurse and this test would prove nothing",
        )
        self._lock()
        # Undo ONE partial, the way the payment widget does. The core then
        # reverses the cash basis entry, and *that* reversal unreconciles the
        # transition account partial, which was not part of the call.
        receivable_partial = invoice.line_ids.filtered(
            lambda line: line.account_id.account_type == "asset_receivable"
        ).matched_credit_ids
        self.assertEqual(len(receivable_partial), 1)
        with patch.object(
            type(self.env["account.move"]),
            "_is_reconcile_lock_in_scope",
            lambda move: bool(move.tax_cash_basis_origin_move_id),
        ):
            invoice.js_remove_outstanding_partial(receivable_partial.id)
        self.assertFalse(
            invoice.line_ids.filtered(
                lambda line: line.account_id.account_type == "asset_receivable"
            ).matched_credit_ids
        )
