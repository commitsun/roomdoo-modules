# Copyright 2026 Commit [Sun]
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
"""Editing a payment from the app (PATCH /transactions/p/<id>), and the channel
updates of a folio: a payment of a locked period is never reset to draft."""
import datetime
from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests import tagged

from odoo.addons.base_rest.tools import ROUTING_DECORATOR_ATTR
from odoo.addons.pms_api_rest.tests.common_invoice import InvoiceServiceCase


@tagged("post_install", "-at_install")
class TestTransactionUpdate(InvoiceServiceCase):
    def setUp(self):
        super().setUp()
        patcher = patch.object(
            type(self.env["res.company"]),
            "_get_rectification_transfer_journal",
            lambda company: self.transfer_journal,
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.today = datetime.date.today()

    def _lock(self, lock_date):
        patcher = patch.object(
            type(self.env["res.company"]),
            "_get_reconcile_lock_date",
            lambda company: lock_date,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _update(self, payment, **values):
        info = self.env.datamodels["pms.transaction.info"](**values)
        payment_id = self._service("transactions").update_transaction(payment.id, info)
        return self.env["account.payment"].browse(payment_id)

    def test_the_refund_endpoint_keeps_its_route(self):
        # Overriding a REST method without its decorator unregisters it.
        service = self._service("folios")
        routing = getattr(
            type(service).create_folio_refund, ROUTING_DECORATOR_ATTR, None
        )
        self.assertTrue(routing)
        self.assertIn("/<int:folio_id>/refund", str(routing))

    def test_in_an_open_period_the_payment_is_reconciled_again(self):
        invoice = self._posted_invoice()
        payment = self._pay(invoice, ref="Before")

        self._update(payment, reference="After")

        self.assertEqual(payment.ref, "After")
        self.assertEqual(payment.state, "posted")
        self.assertIn(invoice, payment.reconciled_invoice_ids)
        self.assertEqual(invoice.payment_state, "paid")

    def test_a_locked_payment_keeps_its_amount_and_registers_the_difference(self):
        invoice = self._posted_invoice()
        payment = self._pay(invoice)
        amount = payment.amount
        self._lock(self.today)

        result = self._update(payment, amount=amount + 20.0)

        self.assertEqual(result, payment)
        self.assertEqual(payment.amount, amount)
        self.assertEqual(payment.state, "posted")
        self.assertIn(invoice, payment.reconciled_invoice_ids)
        correction = payment.amendment_ids
        self.assertEqual(len(correction), 1)
        self.assertEqual(correction.amount, 20.0)
        self.assertEqual(correction.payment_type, "inbound")
        self.assertEqual(correction.folio_ids, payment.folio_ids)

    def test_the_customer_of_a_locked_payment_follows_its_invoice(self):
        invoice = self._posted_invoice()
        payment = self._pay(invoice)
        self._lock(self.today)

        with self.assertRaisesRegex(UserError, "customer of a payment follows"):
            self._update(payment, partnerId=self.company_customer.id)

        self.assertEqual(payment.partner_id, self.guest)
        self.assertIn(invoice, payment.reconciled_invoice_ids)

    def test_a_payment_cannot_be_moved_into_a_closed_period(self):
        invoice = self._posted_invoice()
        payment = self._pay(invoice)
        self._lock(self.today - datetime.timedelta(days=10))

        with self.assertRaisesRegex(UserError, "closed period"):
            self._update(
                payment,
                date=(self.today - datetime.timedelta(days=12)).isoformat(),
            )

        self.assertEqual(payment.date, invoice.date)
        self.assertIn(invoice, payment.reconciled_invoice_ids)

    def test_the_same_date_sent_again_is_no_change(self):
        invoice = self._posted_invoice()
        payment = self._pay(invoice)
        self._lock(self.today)
        self._update(payment, date=payment.date.isoformat(), reference="Same day")
        self.assertEqual(payment.ref, "Same day")
        self.assertFalse(payment.amendment_ids)

    def test_a_channel_update_registers_the_difference_on_a_locked_payment(self):
        invoice = self._posted_invoice()
        payment = self._pay(invoice)
        amount = payment.amount
        self._lock(self.today)
        service = self._service("folios")

        self.assertIn(
            ("amended_payment_id", "=", False), service._ota_payment_extra_domain()
        )
        service._update_ota_payment_amount(payment, amount - 15.0)

        self.assertEqual(payment.amount, amount)
        self.assertEqual(payment.amendment_ids.payment_type, "outbound")
        self.assertEqual(service._ota_payment_amount(payment), amount - 15.0)

    def test_a_refund_cannot_be_dated_in_a_closed_period(self):
        invoice = self._posted_invoice()
        self._lock(self.today)
        info = self.env.datamodels["pms.transaction.info"](
            journalId=self.bank_journal.id,
            amount=10.0,
            date=self.today.isoformat(),
            partnerId=self.guest.id,
        )
        with self.assertRaisesRegex(UserError, "closed period"):
            self._service("folios").create_folio_refund(invoice.folio_ids.id, info)
