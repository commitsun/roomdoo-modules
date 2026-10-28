# Copyright 2026 Commit [Sun]
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
"""The payments API posts as the superuser, for whom the core only applies the
fiscal year close: the monthly close has to be enforced by the API itself."""
import datetime

from fastapi import status

from odoo import Command
from odoo.tests import tagged

from odoo.addons.pms_fastapi.tests.common import CommonTestPmsApiPayment

CLOSED = datetime.date(2026, 1, 15)
LOCK = datetime.date(2026, 1, 31)
OPEN = datetime.date(2026, 2, 10)


@tagged("post_install", "-at_install")
class TestPaymentLockDate(CommonTestPmsApiPayment):
    def _close_month(self):
        self.test_company.sudo().write({"period_lock_date": LOCK})

    def _request(self, method, url, body=None):
        with self._create_test_client(raise_server_exceptions=False) as test_client:
            self._login(test_client)
            return getattr(test_client, method)(url, json=body)

    def _assert_locked(self, response):
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT, response.text)
        self.assertEqual(response.json()["type"], "/errors/fiscal-lock-date")

    def test_the_lock_date_includes_the_monthly_close(self):
        self._close_month()
        helper = self.env["pms_api_payment.payment_router.helper"]
        self.assertEqual(helper._get_payment_lock_date(self.test_company), LOCK)

    def test_a_payment_of_a_closed_month_cannot_be_cancelled(self):
        payment = self._create_payment(pay_date=CLOSED)
        self._close_month()
        self._assert_locked(self._request("post", f"/payments/{payment.id}/cancel"))
        self.assertEqual(payment.state, "posted")

    def test_a_payment_of_an_open_month_can_be_cancelled(self):
        payment = self._create_payment(pay_date=OPEN)
        self._close_month()
        response = self._request("post", f"/payments/{payment.id}/cancel")
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
        self.assertEqual(payment.state, "cancel")

    def test_a_payment_cannot_be_registered_in_a_closed_month(self):
        self._close_month()
        response = self._request(
            "post",
            "/customer-payments",
            {
                "amount": 10.0,
                "date": CLOSED.isoformat(),
                "paymentMethodId": self.bank_inbound.id,
                "reference": "",
            },
        )
        self._assert_locked(response)

    def test_a_supplier_payment_cannot_be_registered_in_a_closed_month(self):
        self._close_month()
        response = self._request(
            "post",
            "/supplier-payments",
            {
                "amount": 10.0,
                "date": CLOSED.isoformat(),
                "paymentMethodId": self.bank_outbound.id,
                "partnerId": self.supplier.id,
                "reference": "",
            },
        )
        self._assert_locked(response)

    def test_a_payment_cannot_be_moved_into_a_closed_month(self):
        payment = self._create_payment(pay_date=OPEN)
        self._close_month()
        response = self._request(
            "patch", f"/customer-payments/{payment.id}", {"date": CLOSED.isoformat()}
        )
        self._assert_locked(response)
        self.assertEqual(payment.date, OPEN)

    def test_a_refund_cannot_be_dated_in_a_closed_month(self):
        folio = self._folio()
        payment = self._create_payment(
            amount=100.0, partner=folio.partner_id, pay_date=OPEN
        )
        payment.folio_ids = [Command.link(folio.id)]
        self._close_month()
        response = self._request(
            "post",
            "/customer-payments/refunds",
            {
                "date": CLOSED.isoformat(),
                "paymentMethodId": self.bank_outbound.id,
                "payments": [{"paymentId": payment.id, "amount": 100.0}],
            },
        )
        self._assert_locked(response)

    def test_an_internal_transfer_cannot_be_created_in_a_closed_month(self):
        self._close_month()
        response = self._request(
            "post",
            "/internal-transfers",
            {
                "amount": 100.0,
                "date": CLOSED.isoformat(),
                "originPaymentMethodId": self.bank_outbound.id,
                "destinationPaymentMethodId": self.bank2_inbound.id,
                "reference": "",
            },
        )
        self._assert_locked(response)

    def test_an_internal_transfer_of_a_closed_month_cannot_be_edited(self):
        transfer = self._create_payment(
            payment_type="outbound", pay_date=CLOSED, is_internal_transfer=True
        )
        self._close_month()
        response = self._request(
            "patch", f"/internal-transfers/{transfer.id}", {"amount": 50.0}
        )
        self._assert_locked(response)
        self.assertEqual(transfer.amount, 100.0)
        self.assertEqual(transfer.state, "posted")

    # -- payments of a locked period ------------------------------------
    def test_the_amount_of_a_payment_of_a_closed_month_cannot_change(self):
        payment = self._create_payment(pay_date=CLOSED)
        self._close_month()
        response = self._request(
            "patch", f"/customer-payments/{payment.id}", {"amount": 130.0}
        )
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT, response.text)
        self.assertEqual(response.json()["type"], "/errors/locked-payment-amount")
        self.assertEqual(payment.amount, 100.0)
        self.assertEqual(payment.state, "posted")
        self.assertFalse(payment.amendment_ids)

    def _settled_payment(self, pay_date, invoice_date=None):
        folio = self._confirmed_folio()
        invoice = self._invoice_for_folio(folio, post=False)
        if invoice_date:
            invoice.invoice_date = invoice_date
        invoice.action_post()
        payment = self._create_payment(
            amount=invoice.amount_total, partner=invoice.partner_id, pay_date=pay_date
        )
        payment.folio_ids = [Command.link(folio.id)]
        (invoice.line_ids + payment.move_id.line_ids).filtered(
            lambda line: line.account_id.account_type == "asset_receivable"
            and not line.reconciled
        ).reconcile()
        self.assertIn(invoice, payment.reconciled_invoice_ids, "fixture not settled")
        return payment, invoice

    def test_the_customer_of_a_locked_payment_settling_an_invoice_is_kept(self):
        payment, invoice = self._settled_payment(CLOSED)
        self._close_month()
        response = self._request(
            "patch",
            f"/customer-payments/{payment.id}",
            {"partnerId": self.supplier.id},
        )
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT, response.text)
        self.assertEqual(response.json()["type"], "/errors/locked-payment-partner")
        self.assertIn(invoice, payment.reconciled_invoice_ids)

    def test_the_date_of_a_locked_payment_cannot_change(self):
        payment = self._create_payment(pay_date=CLOSED)
        self._close_month()
        response = self._request(
            "patch", f"/customer-payments/{payment.id}", {"date": OPEN.isoformat()}
        )
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT, response.text)
        self.assertEqual(response.json()["type"], "/errors/locked-payment-date")
        self.assertEqual(payment.date, CLOSED)

    def test_a_payment_settling_an_invoice_of_a_closed_month_cannot_be_cancelled(self):
        # The invoice belongs to the closed month, the payment does not.
        payment, invoice = self._settled_payment(OPEN, invoice_date=CLOSED)
        self._close_month()
        response = self._request("post", f"/payments/{payment.id}/cancel")
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT, response.text)
        self.assertEqual(payment.state, "posted")
        self.assertIn(invoice, payment.reconciled_invoice_ids)

    def test_the_bypass_group_keeps_the_core_lock_date(self):
        self._close_month()
        helper = self.env["pms_api_payment.payment_router.helper"].with_context(
            bypass_reconcile_lock_date=True
        )
        self.assertNotEqual(helper._get_payment_lock_date(self.test_company), LOCK)

    def test_a_payment_settling_an_invoice_of_a_closed_month_takes_a_new_amount(self):
        # The invoice belongs to the closed month, the payment does not.
        payment, invoice = self._settled_payment(OPEN, invoice_date=CLOSED)
        self._close_month()
        new_amount = invoice.amount_total - 10.0
        response = self._request(
            "patch", f"/customer-payments/{payment.id}", {"amount": new_amount}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
        self.assertEqual(payment.amount, new_amount)
        self.assertEqual(payment.state, "posted")
        self.assertIn(invoice, payment.reconciled_invoice_ids)
        self.assertEqual(invoice.amount_residual, 10.0)
        self.assertFalse(payment.amendment_ids)
