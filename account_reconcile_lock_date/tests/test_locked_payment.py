import datetime
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import tagged

from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.addons.account_reconcile_lock_date.models.account_payment import (
    LockedPaymentError,
)

CLOSED = datetime.date(2026, 9, 20)
LOCK = datetime.date(2026, 9, 30)
OPEN = datetime.date(2026, 10, 2)


@tagged("post_install", "-at_install")
class TestLockedPayment(AccountTestInvoicingCommon):
    """A payment of a locked period is changed itself when it belongs to an
    open period, or refused when it belongs to a closed one. Nothing is ever
    registered beside it."""

    @classmethod
    def setUpClass(cls, chart_template_ref=None):
        super().setUpClass(chart_template_ref=chart_template_ref)
        cls.company = cls.company_data["company"]
        cls.transfer_journal = cls.company_data["default_journal_misc"]
        cls.bank = cls.company_data["default_journal_bank"]

    def setUp(self):
        super().setUp()
        patcher = patch.object(
            type(self.env["res.company"]),
            "_get_rectification_transfer_journal",
            lambda company: self.transfer_journal,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _close(self):
        self.company.period_lock_date = LOCK

    def _invoice(self, amount, date):
        invoice = self.init_invoice(
            "out_invoice",
            partner=self.partner_a,
            invoice_date=date,
            post=False,
            amounts=[amount],
            taxes=[],
        )
        invoice.invoice_payment_term_id = False
        invoice.action_post()
        return invoice

    def _payment(self, amount, date):
        payment = self.env["account.payment"].create(
            {
                "payment_type": "inbound",
                "partner_type": "customer",
                "partner_id": self.partner_a.id,
                "amount": amount,
                "date": date,
                "journal_id": self.bank.id,
            }
        )
        payment.action_post()
        return payment

    @staticmethod
    def _receivable(move):
        return move.line_ids.filtered(
            lambda line: line.account_id.account_type == "asset_receivable"
        )

    def _reconcile(self, invoice, payment):
        (self._receivable(invoice) | self._receivable(payment.move_id)).reconcile()

    # ------------------------------------------------------------------
    # what is locked
    # ------------------------------------------------------------------
    def test_a_payment_of_a_closed_month_is_locked(self):
        payment = self._payment(100.0, CLOSED)
        self._close()
        self.assertTrue(payment._is_reconcile_locked())

    def test_a_payment_settling_an_invoice_of_a_closed_month_is_locked(self):
        invoice = self._invoice(100.0, CLOSED)
        payment = self._payment(100.0, OPEN)
        self._reconcile(invoice, payment)
        self._close()
        self.assertTrue(payment._is_reconcile_locked())

    def test_a_payment_of_an_open_month_is_not_locked(self):
        invoice = self._invoice(100.0, OPEN)
        payment = self._payment(100.0, OPEN)
        self._reconcile(invoice, payment)
        self._close()
        self.assertFalse(payment._is_reconcile_locked())

    def test_the_bypass_context_lets_it_through(self):
        payment = self._payment(100.0, CLOSED)
        self.assertFalse(payment._reconcile_lock_bypassed())
        self.assertTrue(
            payment.with_context(
                bypass_reconcile_lock_date=True
            )._reconcile_lock_bypassed()
        )

    # ------------------------------------------------------------------
    # amount
    # ------------------------------------------------------------------
    def _correction(self, payment, amount, payment_type):
        """A correction as they were registered before 28-10-2026."""
        correction = self.env["account.payment"].create(
            {
                "payment_type": payment_type,
                "partner_type": "customer",
                "partner_id": self.partner_a.id,
                "amount": amount,
                "date": OPEN,
                "journal_id": self.bank.id,
                "amended_payment_id": payment.id,
            }
        )
        correction.action_post()
        return correction

    def _corrections_of(self, payment):
        return self.env["account.payment"].search(
            [("amended_payment_id", "=", payment.id)]
        )

    def test_the_amount_of_a_payment_of_a_closed_month_cannot_change(self):
        payment = self._payment(100.0, CLOSED)
        self._close()
        with self.assertRaises(LockedPaymentError) as error:
            payment._amend_locked_payment({"amount": 130.0})
        self.assertEqual(error.exception.code, "amount")
        self.assertEqual(payment.amount, 100.0)
        self.assertEqual(payment.state, "posted")
        self.assertFalse(self._corrections_of(payment))

    def test_the_same_amount_is_no_change(self):
        payment = self._payment(100.0, CLOSED)
        self._close()
        payment._amend_locked_payment({"amount": 100.0})
        self.assertEqual(payment.amount, 100.0)
        self.assertEqual(payment.state, "posted")

    def test_a_payment_settling_an_invoice_of_a_closed_month_changes_itself(self):
        invoice = self._invoice(100.0, CLOSED)
        payment = self._payment(100.0, OPEN)
        self._reconcile(invoice, payment)
        self._close()
        name = payment.name

        payment._amend_locked_payment({"amount": 70.0})

        self.assertEqual(payment.amount, 70.0)
        self.assertEqual(payment.state, "posted")
        self.assertEqual(payment.name, name)
        self.assertIn(invoice, payment.reconciled_invoice_ids)
        self.assertEqual(invoice.amount_residual, 30.0)
        self.assertFalse(self._corrections_of(payment))

    def test_the_new_amount_settles_the_oldest_invoice_first(self):
        older = self._invoice(60.0, datetime.date(2026, 9, 10))
        newer = self._invoice(40.0, datetime.date(2026, 9, 25))
        payment = self._payment(100.0, OPEN)
        (
            self._receivable(older)
            | self._receivable(newer)
            | self._receivable(payment.move_id)
        ).reconcile()
        self._close()

        payment._amend_locked_payment({"amount": 80.0})

        self.assertEqual(older.amount_residual, 0.0)
        self.assertEqual(newer.amount_residual, 20.0)

    def test_a_higher_amount_stays_open_on_the_payment(self):
        invoice = self._invoice(100.0, CLOSED)
        payment = self._payment(100.0, OPEN)
        self._reconcile(invoice, payment)
        self._close()

        payment._amend_locked_payment({"amount": 130.0})

        self.assertTrue(invoice.currency_id.is_zero(invoice.amount_residual))
        self.assertEqual(self._receivable(payment.move_id).amount_residual, -30.0)

    def test_the_earlier_corrections_still_count(self):
        invoice = self._invoice(100.0, CLOSED)
        payment = self._payment(100.0, OPEN)
        self._reconcile(invoice, payment)
        self._correction(payment, 20.0, "inbound")
        self._close()
        self.assertEqual(payment._amended_amount(), 120.0)

        payment._amend_locked_payment({"amount": 110.0})

        self.assertEqual(payment.amount, 90.0)
        self.assertEqual(payment._amended_amount(), 110.0)
        self.assertEqual(invoice.amount_residual, 10.0)

    def test_the_amount_with_its_corrections_is_rounded(self):
        payment = self._payment(0.1, CLOSED)
        self._correction(payment, 0.2, "inbound")
        self.assertEqual(payment._amended_amount(), 0.3)

    def test_a_payment_reconciled_with_the_bank_cannot_change(self):
        invoice = self._invoice(100.0, CLOSED)
        payment = self._payment(100.0, OPEN)
        self._reconcile(invoice, payment)
        self._close()
        payment.is_matched = True

        with self.assertRaises(LockedPaymentError) as error:
            payment._amend_locked_payment({"amount": 70.0})

        self.assertEqual(error.exception.code, "bank")
        self.assertEqual(payment.amount, 100.0)
        self.assertIn(invoice, payment.reconciled_invoice_ids)

    def test_a_payment_settling_a_down_payment_cannot_change(self):
        invoice = self._invoice(100.0, CLOSED)
        payment = self._payment(100.0, OPEN)
        self._reconcile(invoice, payment)
        self._close()

        with patch.object(
            type(self.env["account.move"]),
            "_is_downpayment",
            lambda move: move == invoice,
        ):
            with self.assertRaises(LockedPaymentError) as error:
                payment._amend_locked_payment({"amount": 70.0})

        self.assertEqual(error.exception.code, "downpayment")
        self.assertEqual(payment.amount, 100.0)
        self.assertIn(invoice, payment.reconciled_invoice_ids)

    def test_a_payment_settling_a_rectified_invoice_cannot_change(self):
        invoice = self._invoice(100.0, CLOSED)
        payment = self._payment(100.0, OPEN)
        self._reconcile(invoice, payment)
        self._close()
        invoice._rectify_keep_reconciliation(OPEN)

        with self.assertRaisesRegex(UserError, "rectified"):
            payment._amend_locked_payment({"amount": 70.0})

        self.assertEqual(payment.amount, 100.0)
        self.assertIn(invoice, payment.reconciled_invoice_ids)

    # ------------------------------------------------------------------
    # reference and journal
    # ------------------------------------------------------------------
    def test_the_reference_is_written_in_place(self):
        payment = self._payment(100.0, CLOSED)
        self._close()
        self.assertFalse(payment._amend_locked_payment({"ref": "Booking 123"}))
        self.assertEqual(payment.ref, "Booking 123")
        self.assertEqual(payment.state, "posted")

    def test_the_journal_cannot_change(self):
        payment = self._payment(100.0, CLOSED)
        self._close()
        with self.assertRaises(LockedPaymentError) as error:
            payment._amend_locked_payment(
                {"journal_id": self.company_data["default_journal_cash"].id}
            )
        self.assertEqual(error.exception.code, "journal")
        self.assertEqual(payment.journal_id, self.bank)

    # ------------------------------------------------------------------
    # date
    # ------------------------------------------------------------------
    def test_the_date_of_a_payment_of_a_closed_month_cannot_change(self):
        payment = self._payment(100.0, CLOSED)
        self._close()
        with self.assertRaises(LockedPaymentError) as error:
            payment._amend_locked_payment({"date": OPEN})
        self.assertEqual(error.exception.code, "date")
        self.assertEqual(payment.date, CLOSED)

    def test_the_same_date_is_no_change(self):
        payment = self._payment(100.0, CLOSED)
        self._close()
        self.assertFalse(payment._amend_locked_payment({"date": CLOSED}))

    def test_a_payment_locked_by_what_it_settles_moves_in_place(self):
        invoice = self._invoice(100.0, CLOSED)
        payment = self._payment(100.0, OPEN)
        self._reconcile(invoice, payment)
        self._close()
        name = payment.name

        payment._amend_locked_payment({"date": datetime.date(2026, 10, 5)})

        self.assertEqual(payment.date, datetime.date(2026, 10, 5))
        self.assertEqual(payment.name, name)
        self.assertEqual(payment.state, "posted")
        self.assertIn(invoice, payment.reconciled_invoice_ids)

    def test_moved_to_another_month_it_takes_a_number_of_that_month(self):
        invoice = self._invoice(100.0, CLOSED)
        payment = self._payment(100.0, OPEN)
        self._reconcile(invoice, payment)
        self._close()

        payment._amend_locked_payment({"date": datetime.date(2026, 11, 3)})

        self.assertEqual(payment.date, datetime.date(2026, 11, 3))
        self.assertTrue(payment.move_id._sequence_matches_date())
        self.assertIn(invoice, payment.reconciled_invoice_ids)

    def test_a_payment_cannot_be_moved_into_a_closed_month(self):
        invoice = self._invoice(100.0, CLOSED)
        payment = self._payment(100.0, OPEN)
        self._reconcile(invoice, payment)
        self._close()
        with self.assertRaises(LockedPaymentError):
            payment._amend_locked_payment({"date": datetime.date(2026, 9, 29)})
        self.assertEqual(payment.date, OPEN)

    # ------------------------------------------------------------------
    # customer
    # ------------------------------------------------------------------
    def test_the_customer_of_a_payment_settling_an_invoice_cannot_change(self):
        invoice = self._invoice(100.0, CLOSED)
        payment = self._payment(100.0, CLOSED)
        self._reconcile(invoice, payment)
        self._close()

        with self.assertRaises(LockedPaymentError) as error:
            payment._amend_locked_payment({"partner_id": self.partner_b.id})

        self.assertEqual(error.exception.code, "partner")
        self.assertIn(invoice.name, str(error.exception))
        self.assertEqual(payment.partner_id, self.partner_a)
        self.assertIn(invoice, payment.reconciled_invoice_ids)

    def test_the_customer_of_an_open_payment_moves_with_a_transfer(self):
        payment = self._payment(100.0, CLOSED)
        self._close()

        payment._amend_locked_payment({"partner_id": self.partner_b.id})

        self.assertEqual(payment.partner_id, self.partner_a)
        self.assertTrue(self._receivable(payment.move_id).reconciled)
        transfer = self.env["account.move"].search(
            [("journal_id", "=", self.transfer_journal.id)]
        )
        self.assertEqual(len(transfer), 1)
        self.assertEqual(transfer.date, fields.Date.context_today(payment))
        new_line = transfer.line_ids.filtered(
            lambda line: line.partner_id == self.partner_b
        )
        self.assertEqual(new_line.credit, 100.0)
        self.assertFalse(new_line.reconciled)

    def test_the_transfer_takes_the_values_of_the_hook(self):
        payment = self._payment(100.0, CLOSED)
        self._close()
        with patch.object(
            type(self.env["account.move"]),
            "_rectification_transfer_vals",
            lambda move: {"narration": "carried by the hook"},
        ):
            transfer = payment._transfer_locked_payment_partner(self.partner_b)
        self.assertIn("carried by the hook", transfer.narration)

    def test_a_customer_change_with_a_new_amount_is_refused_whole(self):
        payment = self._payment(100.0, CLOSED)
        self._close()

        with self.assertRaises(LockedPaymentError) as error:
            payment._amend_locked_payment(
                {"partner_id": self.partner_b.id, "amount": 110.0}
            )

        self.assertEqual(error.exception.code, "amount")
        self.assertEqual(payment.partner_id, self.partner_a)
        self.assertFalse(
            self.env["account.move"].search(
                [("journal_id", "=", self.transfer_journal.id)]
            )
        )
