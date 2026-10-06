from datetime import date

from fastapi import status

from odoo import Command
from odoo.tests import tagged

from odoo.addons.pms_fastapi.tests.common import CommonTestPmsApiPayment


@tagged("post_install", "-at_install")
class TestCustomerPaymentEndpoints(CommonTestPmsApiPayment):
    """POST/GET/PATCH /customer-payments and POST /customer-payments/refunds."""

    def _payment_body(self, **overrides):
        body = {
            "amount": 10.0,
            "date": "2026-03-04",
            "paymentMethodId": self.bank_inbound.id,
            "reference": "",
        }
        body.update(overrides)
        return body

    def _customer_payment_on_folio(self, folio, amount=100.0):
        payment = self._create_payment(amount=amount, partner=folio.partner_id)
        payment.folio_ids = [Command.link(folio.id)]
        return payment

    def _refund_body(self, method_line, payments, refund_date="2026-01-15"):
        return {
            "date": refund_date,
            "paymentMethodId": method_line.id,
            "payments": payments,
        }

    def _mark_payment_reconciled(self, payment):
        """Leave the payment's receivable line reconciled (against an opposite
        manual payment on the same account), so it is no longer fully open —
        without depending on sale journals or taxes."""
        opposite = self._create_payment(
            amount=payment.amount,
            payment_type="outbound",
            partner_type="customer",
            partner=payment.partner_id,
        )
        account = payment.destination_account_id
        lines = (payment.move_id.line_ids + opposite.move_id.line_ids).filtered(
            lambda mline: mline.account_id == account and not mline.reconciled
        )
        lines.reconcile()
        return opposite

    # -- POST /customer-payments --

    def test_payment_without_context(self):
        """A payment with no folio/invoice context is a plain payment."""
        with self._create_test_client() as test_client:
            self._login(test_client)
            response = test_client.post(
                "/customer-payments",
                json=self._payment_body(
                    amount=55.0, partnerId=self.customer.id, reference="206/26/026735"
                ),
            )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.text)
        body = response.json()
        self.assertEqual(body["paymentType"], "customerPayment")
        self.assertEqual(body["amount"], 55.0)
        self.assertEqual(body["partner"]["id"], self.customer.id)
        self.assertEqual(body["reference"], "206/26/026735")
        self.assertIsNone(body["folio"])
        payment = self.env["account.payment"].browse(body["id"])
        self.assertEqual(payment.state, "posted")
        self.assertEqual(payment.journal_id, self.journal_bank)

    def test_payment_from_folio(self):
        """folioId links the payment to the folio."""
        folio = self._folio()
        with self._create_test_client() as test_client:
            self._login(test_client)
            response = test_client.post(
                "/customer-payments", json=self._payment_body(folioId=folio.id)
            )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.text)
        body = response.json()
        self.assertEqual(body["paymentType"], "customerPayment")
        self.assertEqual(body["amount"], 10.0)
        self.assertEqual(body["folio"]["id"], folio.id)
        self.assertIn(body["id"], folio.payment_ids.ids)

    def test_payment_from_invoice_reconciles(self):
        """A payment from invoiceId is registered against the invoice and
        reconciled with it; the folio link is then derived from the reconciled
        invoice (not from do_payment)."""
        folio = self._confirmed_folio()
        invoice = self._invoice_for_folio(folio)
        self.assertIn(folio.id, invoice.folio_ids.ids)
        total = invoice.amount_total
        pay_amount = round(total / 2, 2)
        self.assertGreater(total, pay_amount)  # ensures a partial payment
        with self._create_test_client() as test_client:
            self._login(test_client)
            response = test_client.post(
                "/customer-payments",
                json=self._payment_body(amount=pay_amount, invoiceId=invoice.id),
            )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.text)
        body = response.json()
        self.assertEqual(body["folio"]["id"], folio.id)
        self.assertEqual(
            [inv["id"] for inv in body["invoices"]],
            [invoice.id],
        )
        payment = self.env["account.payment"].browse(body["id"])
        # Reconciled directly against the invoice ...
        self.assertIn(invoice.id, payment.reconciled_invoice_ids.ids)
        # ... which leaves the invoice partially paid ...
        self.assertEqual(invoice.payment_state, "partial")
        self.assertAlmostEqual(invoice.amount_residual, total - pay_amount, places=2)
        # ... and the folio link is the computed one (via the reconciled invoice).
        self.assertIn(folio.id, payment.folio_ids.ids)

    def test_payment_from_invoice_full_marks_paid(self):
        """Paying the full invoice residual marks it as paid."""
        folio = self._confirmed_folio()
        invoice = self._invoice_for_folio(folio)
        with self._create_test_client() as test_client:
            self._login(test_client)
            response = test_client.post(
                "/customer-payments",
                json=self._payment_body(
                    amount=invoice.amount_total, invoiceId=invoice.id
                ),
            )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.text)
        payment = self.env["account.payment"].browse(response.json()["id"])
        self.assertIn(invoice.id, payment.reconciled_invoice_ids.ids)
        self.assertEqual(invoice.payment_state, "paid")

    def test_payment_from_draft_invoice_returns_422(self):
        """A not-confirmed invoice cannot be paid (would crash the register
        wizard); the endpoint rejects it with 422 instead."""
        folio = self._confirmed_folio()
        invoice = self._invoice_for_folio(folio, post=False)
        self.assertEqual(invoice.state, "draft")
        with self._create_test_client(raise_server_exceptions=False) as test_client:
            self._login(test_client)
            response = test_client.post(
                "/customer-payments",
                json=self._payment_body(amount=25.0, invoiceId=invoice.id),
            )
        # 422 literal: 422 is deprecated in
        # newer starlette and the test runner turns the warning into an error.
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(response.json()["type"], "/errors/validation-error")

    def test_invoice_without_folio_returns_422(self):
        """invoiceId of an invoice with no folio is a validation error."""
        invoice = self.env["account.move"].create(
            {
                "move_type": "out_invoice",
                "partner_id": self.customer.id,
                "invoice_line_ids": [
                    (0, 0, {"name": "x", "quantity": 1, "price_unit": 10.0})
                ],
            }
        )
        with self._create_test_client(raise_server_exceptions=False) as test_client:
            self._login(test_client)
            response = test_client.post(
                "/customer-payments",
                json=self._payment_body(amount=25.0, invoiceId=invoice.id),
            )
        # 422 literal: 422 is deprecated in
        # newer starlette and the test runner turns the warning into an error.
        self.assertEqual(response.status_code, 422, response.text)

    def test_amount_not_positive_returns_422(self):
        """amount <= 0 is rejected with 422."""
        with self._create_test_client(raise_server_exceptions=False) as test_client:
            self._login(test_client)
            response = test_client.post(
                "/customer-payments",
                json=self._payment_body(amount=0.0, folioId=self._folio().id),
            )
        # 422 literal: 422 is deprecated in
        # newer starlette and the test runner turns the warning into an error.
        self.assertEqual(response.status_code, 422, response.text)

    def test_folio_and_invoice_mutually_exclusive_returns_422(self):
        folio = self._confirmed_folio()
        invoice = self._invoice_for_folio(folio)
        with self._create_test_client(raise_server_exceptions=False) as test_client:
            self._login(test_client)
            response = test_client.post(
                "/customer-payments",
                json=self._payment_body(folioId=folio.id, invoiceId=invoice.id),
            )
        # 422 literal: 422 is deprecated in
        # newer starlette and the test runner turns the warning into an error.
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(response.json()["type"], "/errors/validation-error")

    def test_outbound_payment_method_returns_422(self):
        """A customer payment collects money, so its method must be inbound."""
        with self._create_test_client(raise_server_exceptions=False) as test_client:
            self._login(test_client)
            response = test_client.post(
                "/customer-payments",
                json=self._payment_body(
                    paymentMethodId=self.bank_outbound.id, partnerId=self.customer.id
                ),
            )
        # 422 literal: 422 is deprecated in
        # newer starlette and the test runner turns the warning into an error.
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(response.json()["type"], "/errors/validation-error")

    def test_unknown_payment_method_returns_404(self):
        with self._create_test_client(raise_server_exceptions=False) as test_client:
            self._login(test_client)
            response = test_client.post(
                "/customer-payments", json=self._payment_body(paymentMethodId=999999)
            )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND, response.text)
        self.assertEqual(response.json()["type"], "/errors/record-not-found")

    def test_cash_payment_auto_opens_session(self):
        """Paying on a cash journal with no open session auto-opens one."""
        Statement = self.env["account.bank.statement"]
        domain = [
            ("journal_id", "=", self.journal_cash.id),
            ("cash_session_closed", "=", False),
        ]
        self.assertFalse(Statement.search(domain))
        with self._create_test_client() as test_client:
            self._login(test_client)
            response = test_client.post(
                "/customer-payments",
                json=self._payment_body(
                    amount=40.0,
                    paymentMethodId=self.cash_inbound.id,
                    partnerId=self.customer.id,
                ),
            )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.text)
        self.assertEqual(len(Statement.search(domain)), 1)

    def test_cash_payment_rolls_back_phantom_session_on_failure(self):
        """A cash payment that fails after the session was auto-opened (here a
        non-existent folio) must roll back the phantom empty cash session
        instead of committing it."""
        Statement = self.env["account.bank.statement"]
        domain = [("journal_id", "=", self.journal_cash.id)]
        self.assertFalse(Statement.search(domain))
        with self._create_test_client() as test_client:
            self._login(test_client)
            response = test_client.post(
                "/customer-payments",
                json=self._payment_body(
                    paymentMethodId=self.cash_inbound.id, folioId=999999999
                ),
            )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND, response.text)
        self.assertFalse(
            Statement.search(domain),
            "A failed cash payment must not leave a phantom cash session.",
        )

    # -- GET /customer-payments/{id} --

    def test_get_detail(self):
        folio = self._folio()
        payment = self._customer_payment_on_folio(folio, amount=100.0)
        with self._create_test_client() as test_client:
            self._login(test_client)
            response = test_client.get(f"/customer-payments/{payment.id}")
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
        body = response.json()
        self.assertEqual(body["id"], payment.id)
        self.assertEqual(body["paymentType"], "customerPayment")
        self.assertEqual(body["availableRefundAmount"], 100.0)
        self.assertEqual(body["folio"]["id"], folio.id)
        self.assertEqual(body["refundBreakdown"], [])
        # Nothing that only applies to another type leaks into the contract.
        self.assertNotIn("originPaymentMethod", body)
        self.assertNotIn("bills", body)

    def test_get_supplier_payment_returns_404(self):
        """The entity only addresses customer payments."""
        supplier_pay = self._create_payment(
            amount=80.0,
            payment_type="outbound",
            partner_type="supplier",
            partner=self.supplier,
        )
        with self._create_test_client(raise_server_exceptions=False) as test_client:
            self._login(test_client)
            response = test_client.get(f"/customer-payments/{supplier_pay.id}")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND, response.text)
        self.assertEqual(response.json()["type"], "/errors/payment-not-found")

    def test_get_internal_transfer_returns_404(self):
        transfer = self._create_payment(
            amount=500.0,
            payment_type="outbound",
            is_internal_transfer=True,
            destination_journal=self.journal_bank2,
        )
        with self._create_test_client(raise_server_exceptions=False) as test_client:
            self._login(test_client)
            response = test_client.get(f"/customer-payments/{transfer.id}")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND, response.text)

    # -- PATCH /customer-payments/{id} --

    def test_update_amount_and_date(self):
        # Same accounting year as the new date: moving a posted entry across
        # years clashes with Odoo's entry-number sequence, unrelated to the API.
        payment = self._create_payment(amount=100.0, pay_date=date(2026, 3, 4))
        with self._create_test_client() as test_client:
            self._login(test_client)
            response = test_client.patch(
                f"/customer-payments/{payment.id}",
                json={"amount": 95.5, "date": "2026-04-10"},
            )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
        body = response.json()
        self.assertEqual(body["id"], payment.id)
        self.assertEqual(body["amount"], 95.5)
        self.assertEqual(body["date"], "2026-04-10")
        self.assertEqual(payment.state, "posted")
        self.assertEqual(payment.amount, 95.5)

    def test_update_partner_and_reference(self):
        """Contact and reference are editable, which the unified PATCH did not
        allow."""
        payment = self._create_payment(amount=100.0, ref="old")
        other_customer = self.env["res.partner"].create({"name": "Other Customer"})
        with self._create_test_client() as test_client:
            self._login(test_client)
            response = test_client.patch(
                f"/customer-payments/{payment.id}",
                json={"partnerId": other_customer.id, "reference": "new ref"},
            )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
        body = response.json()
        self.assertEqual(body["partner"]["id"], other_customer.id)
        self.assertEqual(body["reference"], "new ref")
        self.assertEqual(payment.partner_id, other_customer)
        self.assertEqual(payment.ref, "new ref")

    def test_update_keeps_the_same_id(self):
        """A PATCH never re-registers the payment, so the id is stable."""
        payment = self._create_payment(amount=100.0)
        with self._create_test_client() as test_client:
            self._login(test_client)
            response = test_client.patch(
                f"/customer-payments/{payment.id}", json={"amount": 90.0}
            )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
        self.assertEqual(response.json()["id"], payment.id)
        self.assertEqual(payment.state, "posted")
        self.assertEqual(payment.journal_id, self.journal_bank)

    def test_update_payment_method_is_not_expressible(self):
        """Correcting the account the money went into is a cancellation plus a
        new payment, so the schema rejects the field outright."""
        payment = self._create_payment(amount=100.0)
        with self._create_test_client(raise_server_exceptions=False) as test_client:
            self._login(test_client)
            response = test_client.patch(
                f"/customer-payments/{payment.id}",
                json={"paymentMethodId": self.bank2_inbound.id},
            )
        # 422 literal: 422 is deprecated in
        # newer starlette and the test runner turns the warning into an error.
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(payment.journal_id, self.journal_bank)
        self.assertEqual(payment.state, "posted")

    def test_update_amount_not_positive_returns_422(self):
        payment = self._create_payment(amount=100.0)
        with self._create_test_client(raise_server_exceptions=False) as test_client:
            self._login(test_client)
            response = test_client.patch(
                f"/customer-payments/{payment.id}", json={"amount": 0.0}
            )
        # 422 literal: 422 is deprecated in
        # newer starlette and the test runner turns the warning into an error.
        self.assertEqual(response.status_code, 422, response.text)

    def test_update_unknown_payment_returns_404(self):
        with self._create_test_client(raise_server_exceptions=False) as test_client:
            self._login(test_client)
            response = test_client.patch(
                "/customer-payments/999999", json={"amount": 10.0}
            )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND, response.text)
        self.assertEqual(response.json()["type"], "/errors/payment-not-found")

    def test_update_bank_matched_payment_returns_409(self):
        """A payment already matched against the bank cannot be edited.

        The realistic way it gets matched here is the cash session close, which
        creates the statement line for the shift payment and reconciles it. This
        is the common case: the front will hold payments from closed shifts.
        """
        folio = self._folio()
        with self._create_test_client(raise_server_exceptions=False) as test_client:
            self._login(test_client)
            opened = test_client.post(
                "/cash-sessions",
                json={"journalId": self.journal_cash.id, "baseAmount": 0.0},
            )
            self.assertEqual(opened.status_code, status.HTTP_201_CREATED, opened.text)
            created = test_client.post(
                "/customer-payments",
                json=self._payment_body(
                    amount=120.0,
                    paymentMethodId=self.cash_inbound.id,
                    folioId=folio.id,
                ),
            )
            self.assertEqual(created.status_code, status.HTTP_201_CREATED, created.text)
            payment = self.env["account.payment"].browse(created.json()["id"])
            # The folio context assigns its own reference, so compare against
            # whatever it ended up being rather than the empty string sent.
            ref_before = payment.ref
            closed = test_client.post(
                f"/cash-sessions/{opened.json()['id']}/closing",
                json={"countedCash": 120.0, "note": "shift handover"},
            )
            self.assertEqual(closed.status_code, status.HTTP_200_OK, closed.text)
            self.assertTrue(
                payment.is_matched, "The cash close must match the payment."
            )
            response = test_client.patch(
                f"/customer-payments/{payment.id}",
                json={"amount": 90.0, "reference": "no deberia entrar"},
            )
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT, response.text)
        self.assertEqual(response.json()["type"], "/errors/payment-bank-matched")
        # Nothing was touched: not even the fields that do not need re-posting.
        self.assertEqual(payment.amount, 120.0)
        self.assertEqual(payment.ref, ref_before)
        self.assertEqual(payment.state, "posted")
        self.assertTrue(payment.is_matched)

    def test_update_supplier_payment_returns_404(self):
        """A supplier payment cannot be edited through this entity."""
        supplier_pay = self._create_payment(
            amount=80.0,
            payment_type="outbound",
            partner_type="supplier",
            partner=self.supplier,
        )
        with self._create_test_client(raise_server_exceptions=False) as test_client:
            self._login(test_client)
            response = test_client.patch(
                f"/customer-payments/{supplier_pay.id}", json={"amount": 90.0}
            )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND, response.text)
        self.assertEqual(supplier_pay.amount, 80.0)

    # -- POST /customer-payments/refunds --

    def test_refund_open_payment_reconciles(self):
        """Refunding a fully-open payment creates one customerRefund for the
        total, reconciles it against the payment and zeroes its available."""
        folio = self._folio()
        payment = self._customer_payment_on_folio(folio, amount=100.0)
        with self._create_test_client() as test_client:
            self._login(test_client)
            response = test_client.post(
                "/customer-payments/refunds",
                json=self._refund_body(
                    self.bank_outbound, [{"paymentId": payment.id, "amount": 100.0}]
                ),
            )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.text)
        body = response.json()
        self.assertEqual(body["paymentType"], "customerRefund")
        self.assertEqual(body["amount"], 100.0)
        self.assertEqual(body["availableRefundAmount"], 0.0)
        self.assertEqual(body["folio"]["id"], folio.id)
        # The refund reports which payments it covers, and how much of each.
        self.assertEqual(len(body["refundBreakdown"]), 1)
        breakdown = body["refundBreakdown"][0]
        self.assertEqual(breakdown["paymentId"], payment.id)
        self.assertEqual(breakdown["amount"], 100.0)
        self.assertTrue(breakdown["isReconciled"])
        # The original payment is now fully refunded (nothing left available)
        # and its receivable line reconciled against the refund.
        self.assertEqual(payment.available_refund_amount, 0.0)
        link = self.env["pms.payment.refund.line"].search(
            [("origin_payment_id", "=", payment.id)]
        )
        self.assertEqual(len(link), 1)
        self.assertTrue(link.is_reconciled)
        self.assertEqual(link.refund_payment_id.id, body["id"])
        recv = payment.move_id.line_ids.filtered(
            lambda mline: mline.account_id == payment.destination_account_id
        )
        self.assertTrue(recv.reconciled)

    def test_refunded_payment_reports_its_refunds(self):
        """The breakdown is reported from the refunded payment's side too."""
        folio = self._folio()
        payment = self._customer_payment_on_folio(folio, amount=100.0)
        with self._create_test_client() as test_client:
            self._login(test_client)
            refund = test_client.post(
                "/customer-payments/refunds",
                json=self._refund_body(
                    self.bank_outbound, [{"paymentId": payment.id, "amount": 40.0}]
                ),
            )
            self.assertEqual(refund.status_code, status.HTTP_201_CREATED, refund.text)
            response = test_client.get(f"/customer-payments/{payment.id}")
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
        body = response.json()
        self.assertEqual(body["availableRefundAmount"], 60.0)
        self.assertEqual(len(body["refundBreakdown"]), 1)
        self.assertEqual(body["refundBreakdown"][0]["paymentId"], refund.json()["id"])
        self.assertEqual(body["refundBreakdown"][0]["amount"], 40.0)

    def test_refund_multiple_payments_single_refund(self):
        """N payments of the same folio produce ONE refund for the total."""
        folio = self._folio()
        pay_a = self._customer_payment_on_folio(folio, amount=40.0)
        pay_b = self._customer_payment_on_folio(folio, amount=60.0)
        with self._create_test_client() as test_client:
            self._login(test_client)
            response = test_client.post(
                "/customer-payments/refunds",
                json=self._refund_body(
                    self.bank_outbound,
                    [
                        {"paymentId": pay_a.id, "amount": 40.0},
                        {"paymentId": pay_b.id, "amount": 60.0},
                    ],
                ),
            )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.text)
        self.assertEqual(response.json()["amount"], 100.0)
        self.assertEqual(len(response.json()["refundBreakdown"]), 2)
        self.assertEqual(pay_a.available_refund_amount, 0.0)
        self.assertEqual(pay_b.available_refund_amount, 0.0)
        links = self.env["pms.payment.refund.line"].search(
            [("refund_payment_id", "=", response.json()["id"])]
        )
        self.assertEqual(len(links), 2)

    def test_refund_partial_open_payment(self):
        """A partial refund of an open payment reduces its available amount."""
        folio = self._folio()
        payment = self._customer_payment_on_folio(folio, amount=100.0)
        with self._create_test_client() as test_client:
            self._login(test_client)
            response = test_client.post(
                "/customer-payments/refunds",
                json=self._refund_body(
                    self.bank_outbound, [{"paymentId": payment.id, "amount": 40.0}]
                ),
            )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.text)
        self.assertEqual(payment.available_refund_amount, 60.0)
        recv = payment.move_id.line_ids.filtered(
            lambda mline: mline.account_id == payment.destination_account_id
        )
        # Partial: still open for 60 (contract per-line breakdown respected).
        self.assertAlmostEqual(abs(recv.amount_residual), 60.0)

    def test_refund_already_reconciled_leaves_note_and_does_not_reconcile(self):
        """Refunding an already-reconciled payment is not reconciled again; a
        chatter note is left and the link is marked not reconciled."""
        folio = self._folio()
        payment = self._customer_payment_on_folio(folio, amount=100.0)
        self._mark_payment_reconciled(payment)
        with self._create_test_client() as test_client:
            self._login(test_client)
            response = test_client.post(
                "/customer-payments/refunds",
                json=self._refund_body(
                    self.bank_outbound, [{"paymentId": payment.id, "amount": 100.0}]
                ),
            )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.text)
        refund = self.env["account.payment"].browse(response.json()["id"])
        link = self.env["pms.payment.refund.line"].search(
            [("origin_payment_id", "=", payment.id)]
        )
        self.assertFalse(link.is_reconciled)
        self.assertEqual(payment.available_refund_amount, 0.0)
        self.assertTrue(
            any("already reconciled" in (m.body or "") for m in refund.message_ids)
        )

    def test_refund_exceeds_available_returns_409(self):
        """Requesting more than the available amount returns 409."""
        folio = self._folio()
        payment = self._customer_payment_on_folio(folio, amount=100.0)
        with self._create_test_client(raise_server_exceptions=False) as test_client:
            self._login(test_client)
            response = test_client.post(
                "/customer-payments/refunds",
                json=self._refund_body(
                    self.bank_outbound, [{"paymentId": payment.id, "amount": 150.0}]
                ),
            )
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT, response.text)
        self.assertEqual(response.json()["type"], "/errors/refund-exceeds-available")
        self.assertFalse(
            self.env["pms.payment.refund.line"].search(
                [("origin_payment_id", "=", payment.id)]
            )
        )

    def test_refund_different_folio_returns_409(self):
        """Payments from different folios cannot be refunded together."""
        folio_a = self._folio()
        folio_b = self._folio()
        pay_a = self._customer_payment_on_folio(folio_a, amount=40.0)
        pay_b = self._customer_payment_on_folio(folio_b, amount=40.0)
        with self._create_test_client(raise_server_exceptions=False) as test_client:
            self._login(test_client)
            response = test_client.post(
                "/customer-payments/refunds",
                json=self._refund_body(
                    self.bank_outbound,
                    [
                        {"paymentId": pay_a.id, "amount": 40.0},
                        {"paymentId": pay_b.id, "amount": 40.0},
                    ],
                ),
            )
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT, response.text)
        self.assertEqual(response.json()["type"], "/errors/payment-not-refundable")

    def test_refund_supplier_payment_returns_404(self):
        """A supplier payment is not reachable through the customer entity."""
        folio = self._folio()
        supplier_pay = self._create_payment(
            amount=50.0,
            payment_type="outbound",
            partner_type="supplier",
            partner=self.supplier,
        )
        supplier_pay.folio_ids = [Command.link(folio.id)]
        with self._create_test_client(raise_server_exceptions=False) as test_client:
            self._login(test_client)
            response = test_client.post(
                "/customer-payments/refunds",
                json=self._refund_body(
                    self.bank_outbound, [{"paymentId": supplier_pay.id, "amount": 50.0}]
                ),
            )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND, response.text)
        self.assertEqual(response.json()["type"], "/errors/payment-not-found")

    def test_refund_of_a_refund_returns_409(self):
        """A refund is a customer payment of the entity, but is not refundable."""
        folio = self._folio()
        existing_refund = self._create_payment(
            amount=50.0, payment_type="outbound", partner_type="customer"
        )
        existing_refund.folio_ids = [Command.link(folio.id)]
        with self._create_test_client(raise_server_exceptions=False) as test_client:
            self._login(test_client)
            response = test_client.post(
                "/customer-payments/refunds",
                json=self._refund_body(
                    self.bank_outbound,
                    [{"paymentId": existing_refund.id, "amount": 50.0}],
                ),
            )
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT, response.text)
        self.assertEqual(response.json()["type"], "/errors/payment-not-refundable")

    def test_refund_inbound_method_returns_422(self):
        """The refund method must be outbound."""
        folio = self._folio()
        payment = self._customer_payment_on_folio(folio, amount=100.0)
        with self._create_test_client(raise_server_exceptions=False) as test_client:
            self._login(test_client)
            response = test_client.post(
                "/customer-payments/refunds",
                json=self._refund_body(
                    self.bank_inbound, [{"paymentId": payment.id, "amount": 100.0}]
                ),
            )
        self.assertEqual(
            response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY, response.text
        )

    def test_refund_empty_list_returns_422(self):
        """An empty payments list is rejected by validation."""
        with self._create_test_client(raise_server_exceptions=False) as test_client:
            self._login(test_client)
            response = test_client.post(
                "/customer-payments/refunds",
                json=self._refund_body(self.bank_outbound, []),
            )
        self.assertEqual(
            response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY, response.text
        )

    def test_refund_locked_period_returns_409(self):
        """A refund dated within a locked fiscal period is rejected."""
        folio = self._folio()
        payment = self._customer_payment_on_folio(folio, amount=100.0)
        self.test_company.sudo().write({"fiscalyear_lock_date": date(2026, 1, 31)})
        with self._create_test_client(raise_server_exceptions=False) as test_client:
            self._login(test_client)
            response = test_client.post(
                "/customer-payments/refunds",
                json=self._refund_body(
                    self.bank_outbound,
                    [{"paymentId": payment.id, "amount": 100.0}],
                    refund_date="2026-01-15",
                ),
            )
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT, response.text)
        self.assertEqual(response.json()["type"], "/errors/fiscal-lock-date")
        self.assertFalse(
            self.env["pms.payment.refund.line"].search(
                [("origin_payment_id", "=", payment.id)]
            )
        )
