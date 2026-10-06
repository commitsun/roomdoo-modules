# Copyright 2026 Commit [Sun]
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
"""Editing a posted invoice from the app (PATCH /invoices/p/<id>)."""
import datetime

from odoo.exceptions import UserError
from odoo.tests import tagged

from .common_invoice import InvoiceServiceCase


@tagged("post_install", "-at_install")
class TestInvoiceUpdate(InvoiceServiceCase):
    def test_renaming_lines_of_a_posted_invoice_keeps_its_payment(self):
        """The modal re-sends every line name on save. That used to reset the
        invoice to draft and drop every reconciliation it had."""
        original = self._posted_invoice()
        payment = self._pay(original)

        result = self._patch(original, moveLines=self._renamed_lines(original))

        self.assertEqual(result, original)
        self.assertEqual(original.state, "posted")
        self.assertTrue(payment.is_reconciled)
        self.assertEqual(original.payment_state, "paid")
        self.assertFalse(original.reversal_move_id)
        for line in original.invoice_line_ids.filtered(
            lambda line: line.display_type == "product"
        ):
            self.assertEqual(line.name, "Renamed %s" % line.id)

    def test_saving_a_simplified_invoice_leaves_it_where_it_is(self):
        """The modal never loads isSimplifiedInvoice and sends False on every
        save. Taken literally, a plain save of a simplified invoice rectified
        it into a normal invoice for the anonymous customer."""
        original = self._posted_invoice(self.anonymous)
        self.assertEqual(original.journal_id, self.simplified_journal)
        payment = self._pay(original)

        result = self._patch(
            original,
            partnerId=self.anonymous.id,
            isSimplifiedInvoice=False,
            moveLines=self._renamed_lines(original),
        )

        self.assertEqual(result, original)
        self.assertEqual(original.journal_id, self.simplified_journal)
        self.assertFalse(original.reversal_move_id)
        self.assertTrue(payment.is_reconciled)

    def test_a_named_customer_takes_a_simplified_invoice_to_the_normal_journal(
        self,
    ):
        original = self._posted_invoice(self.anonymous)

        new = self._patch(
            original,
            partnerId=self.company_customer.id,
            isSimplifiedInvoice=False,
        )

        self.assertNotEqual(new, original)
        self.assertEqual(new.partner_id, self.company_customer)
        self.assertEqual(new.journal_id, self.normal_journal)
        self.assertEqual(new.state, "posted")

    def test_simplified_without_a_customer_goes_to_the_anonymous_customer(self):
        """Removing the customer and ticking 'simplified' left the named
        customer, with its VAT number, on a simplified invoice."""
        original = self._posted_invoice(self.company_customer)
        self.assertEqual(original.journal_id, self.normal_journal)

        new = self._patch(original, isSimplifiedInvoice=True)

        self.assertNotEqual(new, original)
        self.assertEqual(new.partner_id, self.anonymous)
        self.assertEqual(new.journal_id, self.simplified_journal)
        self.assertEqual(new.state, "posted")

    def test_ticking_simplified_with_the_customer_loaded_gives_an_anonymous_one(
        self,
    ):
        """The modal hides the loaded customer once the option shows, so the
        customer comes back unchanged with the flag: a simplified invoice for
        that named customer was issued instead of an anonymous ticket."""
        original = self._posted_invoice(self.company_customer)

        new = self._patch(
            original,
            partnerId=self.company_customer.id,
            isSimplifiedInvoice=True,
            moveLines=self._renamed_lines(original),
        )

        self.assertNotEqual(new, original)
        self.assertEqual(new.partner_id, self.anonymous)
        self.assertEqual(new.journal_id, self.simplified_journal)

    def test_a_named_customer_gets_a_normal_invoice_whatever_the_flag(self):
        original = self._posted_invoice(self.anonymous)

        new = self._patch(
            original, partnerId=self.company_customer.id, isSimplifiedInvoice=True
        )

        self.assertEqual(new.partner_id, self.company_customer)
        self.assertEqual(new.journal_id, self.normal_journal)

    def test_removing_a_whole_reservation_drops_its_section(self):
        """The modal never sends sections: the one of a reservation whose lines
        were all removed was deleted after the new invoice was posted, which the
        core refuses, and the whole edit failed."""
        today = datetime.date.today()
        first = self.env["pms.reservation"].create(
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
        folio = first.folio_id
        self.env["pms.reservation"].create(
            {
                "folio_id": folio.id,
                "pms_property_id": self.property.id,
                "checkin": today - datetime.timedelta(days=5),
                "checkout": today - datetime.timedelta(days=3),
                "adults": 2,
                "room_type_id": self.room_type.id,
                "partner_id": self.guest.id,
                "sale_channel_origin_id": self.sale_channel.id,
            }
        )
        # The way the app invoices a folio: its sections go in at quantity 0.
        lines = folio.sale_line_ids.filtered(lambda line: not line.is_downpayment)
        original = folio._create_invoices(
            lines_to_invoice={
                line.id: line.qty_to_invoice if not line.display_type else 0
                for line in lines
            },
            partner_invoice_id=self.guest.id,
            final=True,
        ).filtered(lambda move: move.move_type == "out_invoice")
        original.invoice_payment_term_id = False
        original.action_post()

        def sections(move):
            return move.invoice_line_ids.filtered(
                lambda line: line.display_type == "line_section"
            )

        self.assertEqual(len(sections(original)), 2, "fixture: two sections")
        kept = original.invoice_line_ids.filtered(
            lambda line: line.display_type == "product"
            and line.folio_line_ids.reservation_id == first
        )
        line_info = self.env.datamodels["pms.invoice.line.info"]

        new = self._patch(
            original,
            moveLines=[
                line_info(id=line.id, name=line.name, quantity=line.quantity)
                for line in kept
            ],
        )

        self.assertNotEqual(new, original)
        self.assertEqual(new.state, "posted")
        self.assertEqual(len(sections(new)), 1)

    def test_a_reversed_invoice_cannot_be_updated(self):
        original = self._posted_invoice()
        self._patch(original, partnerId=self.company_customer.id)
        self.assertEqual(original.payment_state, "reversed")
        with self.assertRaises(UserError):
            self._patch(original, partnerId=self.guest.id)

    def test_an_invoice_its_credit_notes_cover_cannot_be_updated(self):
        """Rectified keeping its payment, an invoice stays 'paid'. It is the
        credit notes that tell it no longer stands."""
        original = self._posted_invoice()
        self._pay(original)
        credit_note = original._reverse_moves(
            [{"date": original.date, "invoice_date": original.date}], cancel=False
        )
        credit_note.action_post()
        self.assertEqual(original.payment_state, "paid")

        with self.assertRaises(UserError):
            self._patch(original, partnerId=self.company_customer.id)

    def test_the_channel_cannot_change_the_payment_of_a_rectified_invoice(self):
        """It would make the rectified invoice owe it again. The rest of the
        update goes through, and the folio says what was left undone."""
        original = self._posted_invoice()
        folio = original.folio_ids
        payment = self._pay(original, ref="%s - OTA-1" % folio.name)
        credit_note = original._reverse_moves(
            [{"date": original.date, "invoice_date": original.date}], cancel=False
        )
        credit_note.action_post()
        transaction = self.env.datamodels["pms.transaction.info"](
            reference="OTA-1",
            journalId=self.bank_journal.id,
            transactionType="inbound",
            amount=payment.amount - 10.0,
            date=datetime.date.today().strftime("%Y-%m-%d"),
        )

        self._service("folios").compute_transactions(folio, [transaction])

        self.assertEqual(payment.state, "posted")
        self.assertEqual(payment.amount, original.amount_total)
        self.assertTrue(payment.is_reconciled)
        self.assertIn("was not updated", folio.message_ids[:1].body or "", "no warning")

    def test_the_channel_warning_is_posted_once(self):
        """The channel sends the same amount again with every update of the
        reservation."""
        original = self._posted_invoice()
        folio = original.folio_ids
        payment = self._pay(original, ref="%s - OTA-3" % folio.name)
        credit_note = original._reverse_moves(
            [{"date": original.date, "invoice_date": original.date}], cancel=False
        )
        credit_note.action_post()
        transaction = self.env.datamodels["pms.transaction.info"](
            reference="OTA-3",
            journalId=self.bank_journal.id,
            transactionType="inbound",
            amount=payment.amount - 10.0,
            date=datetime.date.today().strftime("%Y-%m-%d"),
        )

        service = self._service("folios")
        service.compute_transactions(folio, [transaction])
        service.compute_transactions(folio, [transaction])

        warnings = folio.message_ids.filtered(
            lambda msg: "was not updated" in (msg.body or "")
        )
        self.assertEqual(len(warnings), 1)

    def test_the_channel_still_changes_the_payment_of_a_live_invoice(self):
        original = self._posted_invoice()
        folio = original.folio_ids
        payment = self._pay(original, ref="%s - OTA-2" % folio.name)
        transaction = self.env.datamodels["pms.transaction.info"](
            reference="OTA-2",
            journalId=self.bank_journal.id,
            transactionType="inbound",
            amount=payment.amount - 10.0,
            date=datetime.date.today().strftime("%Y-%m-%d"),
        )

        self._service("folios").compute_transactions(folio, [transaction])

        self.assertEqual(payment.amount, original.amount_total - 10.0)
