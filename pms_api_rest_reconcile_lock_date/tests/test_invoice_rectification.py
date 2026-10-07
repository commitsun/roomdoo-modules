# Copyright 2026 Commit [Sun]
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
"""Editing a posted invoice from the app (PATCH /invoices/p/<id>).

In a locked period it must not unreconcile it: the original keeps the payment
it was settled with, and the money reaches the new invoice through the credit
note. In an open period the original is reversed the core way, and the new
invoice takes over its payments."""
import datetime
from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests import tagged

from odoo.addons.pms_api_rest.tests.common_invoice import InvoiceServiceCase


@tagged("post_install", "-at_install")
class TestInvoiceRectification(InvoiceServiceCase):
    def setUp(self):
        super().setUp()
        # The company opts in by having a transfer journal, which
        # pms_downpayment_lifecycle configures in production.
        patcher = patch.object(
            type(self.env["res.company"]),
            "_get_rectification_transfer_journal",
            lambda company: self.transfer_journal,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _transfers(self):
        return self.env["account.move"].search(
            [("journal_id", "=", self.transfer_journal.id)]
        )

    def _close_period(self):
        """Lock the period the invoices built here belong to (they are dated
        around today)."""
        patcher = patch.object(
            type(self.env["res.company"]),
            "_get_reconcile_lock_date",
            lambda company: datetime.date.today(),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    # ------------------------------------------------------------------
    # an open period
    # ------------------------------------------------------------------
    def test_in_an_open_period_the_new_invoice_takes_the_payment(self):
        original = self._posted_invoice()
        payment = self._pay(original)

        new = self._patch(original, partnerId=self.company_customer.id)

        self.assertEqual(new.partner_id, self.company_customer)
        self.assertEqual(original.payment_state, "reversed")
        self.assertEqual(payment.reconciled_invoice_ids, new)
        self.assertEqual(new.amount_residual, 0.0)
        self.assertFalse(self._transfers())

    def test_in_an_open_period_a_simplified_invoice_reissued_takes_the_payment(self):
        original = self._posted_invoice(self.anonymous)
        payment = self._pay(original)

        new = self._patch(
            original, partnerId=self.company_customer.id, isSimplifiedInvoice=False
        )

        self.assertEqual(new.journal_id, self.normal_journal)
        self.assertEqual(original.payment_state, "reversed")
        self.assertEqual(payment.reconciled_invoice_ids, new)
        self.assertFalse(self._transfers())

    # ------------------------------------------------------------------
    # a locked period
    # ------------------------------------------------------------------

    def test_customer_change_keeps_the_payment_on_the_original(self):
        original = self._posted_invoice()
        payment = self._pay(original)
        self._close_period()

        new = self._patch(original, partnerId=self.company_customer.id)

        self.assertNotEqual(new, original)
        self.assertEqual(new.state, "posted")
        self.assertEqual(new.partner_id, self.company_customer)
        # The original never let go of its payment.
        self.assertEqual(original.payment_state, "paid")
        self.assertTrue(payment.is_reconciled)
        self.assertEqual(payment.move_id.line_ids.partner_id, self.guest)
        self.assertTrue(original._is_rectified())
        # The money reached the new invoice through a transfer entry.
        self.assertEqual(new.payment_state, "paid")
        self.assertEqual(len(self._transfers()), 1)

    def test_a_simplified_invoice_reissued_to_a_customer_keeps_its_payment(self):
        """The most common edit: the anonymous simplified invoice is reissued
        as a normal invoice for the guest who asks for one."""
        original = self._posted_invoice(self.anonymous)
        payment = self._pay(original)
        self._close_period()

        new = self._patch(
            original, partnerId=self.company_customer.id, isSimplifiedInvoice=False
        )

        self.assertEqual(new.journal_id, self.normal_journal)
        self.assertEqual(new.payment_state, "paid")
        self.assertEqual(original.payment_state, "paid")
        self.assertTrue(payment.is_reconciled)
        transfer = self._transfers()
        self.assertEqual(transfer.line_ids.filtered("debit").partner_id, self.anonymous)
        self.assertEqual(
            transfer.line_ids.filtered("credit").partner_id, self.company_customer
        )

    def test_a_rectified_invoice_cannot_be_edited_again(self):
        original = self._posted_invoice()
        self._pay(original)
        self._close_period()
        self._patch(original, partnerId=self.company_customer.id)
        with self.assertRaises(UserError):
            self._patch(original, partnerId=self.guest.id)

    def test_an_unpaid_invoice_is_still_reversed(self):
        original = self._posted_invoice()
        new = self._patch(original, partnerId=self.company_customer.id)
        self.assertEqual(original.payment_state, "reversed")
        self.assertEqual(new.state, "posted")
        self.assertFalse(self._transfers())

    def test_the_payment_of_the_rectified_invoice_registers_the_difference(self):
        """Reset to draft, the payment would leave the rectified invoice owing
        what the new one already received. In a closed period it is never reset:
        the difference is registered as a refund dated today."""
        original = self._posted_invoice()
        payment = self._pay(original)
        self._close_period()
        self._patch(original, partnerId=self.company_customer.id)
        amount = payment.amount
        info = self.env.datamodels["pms.transaction.info"](amount=amount - 10.0)

        self._service("transactions").update_transaction(payment.id, info)

        self.assertEqual(payment.amount, amount)
        self.assertEqual(payment.state, "posted")
        self.assertEqual(original.payment_state, "paid")
        self.assertIn(original, payment.reconciled_invoice_ids)
        self.assertEqual(payment.amendment_ids.payment_type, "outbound")
        self.assertEqual(payment.amendment_ids.amount, 10.0)
