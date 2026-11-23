from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged

from .test_acc_reconcile import ReconcileFixtureMixin

TOOL = "acc_payment_register"


@tagged("post_install", "-at_install")
class TestAccPaymentRegister(ReconcileFixtureMixin, TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_reconcile([TOOL])
        cls.inv100 = cls._move("out_invoice", cls.customer, 100, cls.prop)
        cls.inv50 = cls._move("out_invoice", cls.customer, 50, cls.prop)
        cls.inv30 = cls._move("out_invoice", cls.customer, 30, None)
        cls.other_inv = cls._move("out_invoice", cls.other_customer, 20, cls.prop)
        cls.foreign = cls._move("out_invoice", cls.customer, 70, cls.other_prop)
        cls.bill = cls._move("in_invoice", cls.customer, 40, cls.prop)

    def _payments(self, partner=None):
        return self.env["account.payment"].search(
            [("partner_id", "=", (partner or self.customer).id)]
        )

    def _register(self, invoices, **kwargs):
        kwargs.setdefault("journal", self.bank.code)
        kwargs["invoices"] = [move.name for move in invoices]
        preview = self._run(TOOL, **kwargs)
        return preview, kwargs

    def test_preview_changes_nothing_then_one_payment(self):
        preview, kwargs = self._register(self.inv100, payment_date="2026-10-20")
        self.assertFalse(self._payments())
        self.assertEqual(self.inv100.amount_residual, 100.0)
        payment = preview["result"]["payment"]
        self.assertEqual(payment["payment_type"], "inbound")
        self.assertEqual(payment["amount"], 100.0)
        self.assertEqual(payment["journal"], self.bank.code)
        self.assertEqual(payment["date"], "2026-10-20")
        (row,) = preview["result"]["invoices"]
        self.assertEqual((row["residual_after"], row["becomes"]), (0.0, "paid"))

        applied = self._confirm(TOOL, preview, **kwargs)

        payments = self._payments()
        self.assertEqual(len(payments), 1)
        self.assertEqual(payments.amount, 100.0)
        self.assertEqual(payments.state, "posted")
        self.assertEqual(payments.journal_id, self.bank)
        self.assertEqual(str(payments.date), "2026-10-20")
        self.assertEqual(payments.payment_type, "inbound")
        self.assertIn(self.inv100.payment_state, ("paid", "in_payment"))
        self.assertEqual(applied["result"]["payments"][0]["payment_id"], payments.id)
        self.assertEqual(applied["result"]["invoices"][0]["residual"], 0.0)

    def test_partial_amount_for_a_single_invoice(self):
        preview, kwargs = self._register(self.inv100, amount=40.0, memo="AI memo")
        (row,) = preview["result"]["invoices"]
        self.assertEqual(
            (row["paid_now"], row["residual_after"], row["becomes"]),
            (40.0, 60.0, "partially_paid"),
        )
        self._confirm(TOOL, preview, **kwargs)
        self.assertEqual(self.inv100.amount_residual, 60.0)
        self.assertEqual(self.inv100.payment_state, "partial")
        self.assertEqual(self._payments().ref, "AI memo")

    def test_several_invoices_get_one_payment(self):
        preview, kwargs = self._register(self.inv50 | self.inv30)
        self.assertEqual(preview["result"]["payment"]["amount"], 80.0)
        self.assertEqual(
            {row["becomes"] for row in preview["result"]["invoices"]}, {"paid"}
        )
        self._confirm(TOOL, preview, **kwargs)
        payments = self._payments()
        self.assertEqual(len(payments), 1)
        self.assertEqual(payments.amount, 80.0)
        for invoice in self.inv50 | self.inv30:
            self.assertIn(invoice.payment_state, ("paid", "in_payment"))

    def test_vendor_bill_gets_an_outgoing_payment(self):
        preview, kwargs = self._register(self.bill)
        self.assertEqual(preview["result"]["payment"]["payment_type"], "outbound")
        self._confirm(TOOL, preview, **kwargs)
        payment = self._payments()
        self.assertEqual(payment.payment_type, "outbound")
        self.assertIn(self.bill.payment_state, ("paid", "in_payment"))

    def test_token_is_bound_to_the_arguments(self):
        preview, kwargs = self._register(self.inv100)
        with self.assertRaisesRegex(UserError, "does not match"):
            self._confirm(TOOL, preview, **dict(kwargs, amount=99.0))
        self.assertFalse(self._payments())

    def test_refusals(self):
        paid, _kwargs = self._register(self.inv30)
        self._confirm(TOOL, paid, **_kwargs)
        cases = (
            ({"invoices": self.inv100 | self.other_inv}, "different partners"),
            ({"invoices": self.inv100 | self.bill}, "only customer invoices"),
            ({"invoices": self.inv30}, "Already paid"),
            ({"invoices": self.foreign}, "No posted entry"),
            (
                {"invoices": self.inv100 | self.inv50, "amount": 10.0},
                "needs a single invoice",
            ),
            ({"invoices": self.inv100, "amount": 100.01}, "greater than"),
            ({"invoices": self.inv100, "amount": 0.0}, "greater than zero"),
            (
                {"invoices": self.inv100, "journal": self.sale_journal.code},
                "No single bank or cash journal",
            ),
        )
        for kwargs, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(
                UserError, message
            ):
                self._register(**kwargs)
        with self.assertRaisesRegex(UserError, "at most 20"):
            self._run(TOOL, invoices=[str(n) for n in range(21)], journal="X")
        self.assertEqual(len(self._payments()), 1)
        # The other hotel is in scope for a user of both hotels.
        preview = self._run(
            TOOL,
            user=self.reader_both,
            invoices=[self.foreign.name],
            journal=self.bank.code,
        )
        self.assertEqual(preview["result"]["payment"]["amount"], 70.0)

    def test_lock_date_is_respected(self):
        self.env.company.period_lock_date = "2026-10-31"
        with self.assertRaisesRegex(UserError, "after the lock date"):
            self._register(self.inv100, payment_date="2026-10-15")
        preview, _kwargs = self._register(self.inv100, payment_date="2026-11-02")
        self.assertEqual(preview["result"]["payment"]["date"], "2026-11-02")

    def test_is_a_write_tool(self):
        self._assert_write_tool(TOOL)
