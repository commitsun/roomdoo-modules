import datetime

from odoo.tests import tagged

from odoo.addons.queue_job.tests.common import trap_jobs

from .common import DownpaymentCase


@tagged("post_install", "-at_install")
class TestDownpaymentProforma(DownpaymentCase):
    """What happens to a down payment when the invoice that absorbs it was
    drafted before it existed, and how the association is decided when more
    than one reading is possible."""

    def _final(self, folio, partner, amount, downpayment_lines=None):
        """A final invoice for the stay. `downpayment_lines` links the down
        payment's folio.sale.line to a line of the invoice, which is exactly
        how pms marks a down payment as already discounted."""
        vals = {
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
        invoice = self.env["account.move"].create(vals)
        if downpayment_lines:
            invoice.write(
                {
                    "invoice_line_ids": [
                        (
                            0,
                            0,
                            {
                                "name": "Down payment",
                                "quantity": 1,
                                "price_unit": -downpayment_lines[1],
                                "tax_ids": [(6, 0, [])],
                                "folio_line_ids": [(6, 0, downpayment_lines[0].ids)],
                            },
                        )
                    ]
                }
            )
        invoice.invoice_payment_term_id = False
        return invoice

    # ------------------------------------------------------------------
    def test_a_down_payment_already_applied_is_left_alone(self):
        """The invoice already discounted it. Rectifying on top would credit
        the customer twice."""
        folio = self._folio()
        downpayment, line = self._downpayment(folio, self.guest, 50.0)
        self._collect(downpayment)

        final = self._final(folio, self.guest, 200.0, downpayment_lines=(line, 50.0))

        self.assertNotIn(downpayment, final._get_downpayments_to_transfer())
        with trap_jobs() as trap:
            final.action_post()
            trap.assert_jobs_count(0)
        self.assertFalse(final.downpayment_transfer_state)

    def test_a_down_payment_the_draft_never_discounted_is_rectified(self):
        """The proforma was drafted before the down payment existed: same
        customer, nothing discounted, so it is rectified and its credit note
        settles against the invoice -- no transfer entry, there is no balance
        to move."""
        folio = self._folio()
        # The proforma is drafted first, by pms, when no down payment exists.
        final = self._final_invoice_via_pms(folio, self.guest)
        self.assertEqual(final.state, "draft")
        downpayment, _line = self._downpayment(folio, self.guest, 50.0)
        self._collect(downpayment)

        self.assertIn(downpayment, final._get_downpayments_to_transfer())
        final.action_post()
        final._transfer_downpayments()

        self.assertEqual(final.downpayment_transfer_state, "done")
        credit_note = self.env["account.move"].search(
            [("reversed_entry_id", "=", downpayment.id)]
        )
        self.assertEqual(len(credit_note), 1)
        self.assertEqual(credit_note.amount_total, 50.0)
        self.assertEqual(credit_note.amount_residual, 0.0)
        # No miscellaneous entry: same customer, same account.
        self.assertFalse(
            self.env["account.move"].search(
                [("move_type", "=", "entry"), ("ref", "like", downpayment.name)]
            )
        )
        # The customer owes the stay less the down payment.
        self.assertEqual(final.amount_residual, final.amount_total - 50.0)

    def test_more_standing_than_this_invoice_bills_is_left_for_review(self):
        folio = self._folio()
        downpayment, _line = self._downpayment(folio, self.guest, 300.0)
        self._collect(downpayment)

        final = self._final(folio, self.guest, 100.0)
        final.action_post()
        final._transfer_downpayments()

        self.assertEqual(final.downpayment_transfer_state, "manual")
        self.assertIn("300", final.downpayment_transfer_message)
        self.assertFalse(
            self.env["account.move"].search(
                [("reversed_entry_id", "=", downpayment.id)]
            ),
            "nothing may be posted while the association is a guess",
        )

    def test_a_partially_refunded_down_payment_is_still_seen(self):
        """The regression that motivated dropping qty_invoiced: a partial
        credit note takes a whole unit off it and the down payment used to
        vanish from the selector with money still standing."""
        folio = self._folio()
        downpayment, line = self._downpayment(folio, self.guest, 100.0)
        self._collect(downpayment)
        downpayment._rectify_downpayment(datetime.date.today(), amount=40.0)
        self.assertEqual(line.qty_invoiced, 0.0, "pms zeroes it, that is the trap")
        self.assertEqual(downpayment._amount_open_to_rectify(), 60.0)

        final = self._final(folio, self.guest, 200.0)
        self.assertIn(
            downpayment,
            final._get_downpayments_to_transfer(),
            "60 are still standing, the down payment must not disappear",
        )
