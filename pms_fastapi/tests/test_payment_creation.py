from fastapi import status

from odoo import Command
from odoo.tests import tagged

from odoo.addons.pms_fastapi.tests.common import CommonTestPmsApi


@tagged("post_install", "-at_install")
class TestPaymentCreationEndpoints(CommonTestPmsApi):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        company = cls.test_company
        if not company.chart_template_id:
            coa = cls.env.ref("l10n_generic_coa.configurable_chart_template", False)
            if not coa:
                coa = cls.env["account.chart.template"].search(
                    [("visible", "=", True)], limit=1
                )
            if not coa:
                cls.skipTest(cls, "No chart of accounts available.")
            coa.try_loading(company=company, install_demo=False)
        cls.env.user.write(
            {
                "company_ids": [Command.link(company.id)],
                "company_id": company.id,
            }
        )
        cls.env = cls.env(
            context=dict(cls.env.context, allowed_company_ids=company.ids)
        )
        cls.journal_cash = cls.env["account.journal"].create(
            {
                "name": "Cash Reception",
                "type": "cash",
                "code": "CSHP",
                "company_id": company.id,
                "pms_property_ids": [Command.set([cls.test_property.id])],
            }
        )
        cls.journal_bank = cls.env["account.journal"].create(
            {
                "name": "Bank PMS",
                "type": "bank",
                "code": "BNKP",
                "company_id": company.id,
                "pms_property_ids": [Command.set([cls.test_property.id])],
            }
        )
        cls.journal_bank2 = cls.env["account.journal"].create(
            {
                "name": "Bank PMS 2",
                "type": "bank",
                "code": "BNKP2",
                "company_id": company.id,
                "pms_property_ids": [Command.set([cls.test_property.id])],
            }
        )
        cls.bank_inbound = cls.journal_bank.inbound_payment_method_line_ids[:1]
        cls.bank_outbound = cls.journal_bank.outbound_payment_method_line_ids[:1]
        cls.bank2_inbound = cls.journal_bank2.inbound_payment_method_line_ids[:1]
        cls.bank2_outbound = cls.journal_bank2.outbound_payment_method_line_ids[:1]
        cls.cash_outbound = cls.journal_cash.outbound_payment_method_line_ids[:1]
        cls.customer = cls.env["res.partner"].create({"name": "Pay Customer"})
        cls.supplier = cls.env["res.partner"].create({"name": "Pay Supplier"})
        cls.product = cls.env["product.product"].create(
            {"name": "Service", "type": "service"}
        )

    def _folio(self):
        return self.env["pms.folio"].create(
            {
                "pms_property_id": self.test_property.id,
                "partner_name": self.customer.name,
                "partner_id": self.customer.id,
            }
        )

    def _invoice_for_folio(self, folio, amount=100.0):
        line = self.env["folio.sale.line"].create(
            {
                "folio_id": folio.id,
                "name": "Stay",
                "product_id": self.product.id,
                "product_uom": self.product.uom_id.id,
                "product_uom_qty": 1,
                "price_unit": amount,
            }
        )
        return self.env["account.move"].create(
            {
                "move_type": "out_invoice",
                "partner_id": self.customer.id,
                "folio_ids": [(6, 0, folio.ids)],
                "invoice_line_ids": [
                    (
                        0,
                        0,
                        {
                            "name": "Stay",
                            "product_id": self.product.id,
                            "quantity": 1,
                            "price_unit": amount,
                            "folio_line_ids": [(6, 0, line.ids)],
                        },
                    )
                ],
            }
        )

    # -- POST /payments --

    def test_supplier_payment(self):
        """supplierPayment with no folio/invoice context → simple payment."""
        with self._create_test_client() as test_client:
            self._login(test_client)
            response = test_client.post(
                "/payments",
                json={
                    "paymentType": "supplierPayment",
                    "amount": 80.0,
                    "date": "2026-03-04",
                    "paymentMethodId": self.bank_outbound.id,
                    "partnerId": self.supplier.id,
                    "reference": "206/26/026735",
                },
            )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.text)
        body = response.json()
        self.assertEqual(body["paymentType"], "supplierPayment")
        self.assertEqual(body["amount"], 80.0)
        self.assertEqual(body["partner"]["id"], self.supplier.id)
        self.assertEqual(body["reference"], "206/26/026735")
        payment = self.env["account.payment"].browse(body["id"])
        self.assertEqual(payment.state, "posted")
        self.assertEqual(payment.journal_id, self.journal_bank)

    def test_customer_payment_from_folio(self):
        """customerPayment with folioId links the payment to the folio."""
        folio = self._folio()
        with self._create_test_client() as test_client:
            self._login(test_client)
            response = test_client.post(
                "/payments",
                json={
                    "paymentType": "customerPayment",
                    "amount": 10.0,
                    "date": "2026-03-04",
                    "paymentMethodId": self.bank_inbound.id,
                    "partnerId": None,
                    "folioId": folio.id,
                    "reference": "",
                },
            )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.text)
        body = response.json()
        self.assertEqual(body["paymentType"], "customerPayment")
        self.assertEqual(body["amount"], 10.0)
        self.assertEqual(body["folio"]["id"], folio.id)
        self.assertIn(body["id"], folio.payment_ids.ids)

    def test_customer_payment_from_invoice(self):
        """customerPayment with invoiceId derives the folio from the invoice."""
        folio = self._folio()
        invoice = self._invoice_for_folio(folio)
        self.assertIn(folio.id, invoice.folio_ids.ids)
        with self._create_test_client() as test_client:
            self._login(test_client)
            response = test_client.post(
                "/payments",
                json={
                    "paymentType": "customerPayment",
                    "amount": 25.0,
                    "date": "2026-03-04",
                    "paymentMethodId": self.bank_inbound.id,
                    "invoiceId": invoice.id,
                    "reference": "",
                },
            )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.text)
        self.assertEqual(response.json()["folio"]["id"], folio.id)

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
                "/payments",
                json={
                    "paymentType": "customerPayment",
                    "amount": 25.0,
                    "date": "2026-03-04",
                    "paymentMethodId": self.bank_inbound.id,
                    "invoiceId": invoice.id,
                },
            )
        # 422 literal: 422 is deprecated in
        # newer starlette and the test runner turns the warning into an error.
        self.assertEqual(response.status_code, 422, response.text)

    def test_amount_not_positive_returns_422(self):
        """amount <= 0 is rejected with 422."""
        with self._create_test_client(raise_server_exceptions=False) as test_client:
            self._login(test_client)
            response = test_client.post(
                "/payments",
                json={
                    "paymentType": "customerPayment",
                    "amount": 0.0,
                    "date": "2026-03-04",
                    "paymentMethodId": self.bank_inbound.id,
                    "folioId": self._folio().id,
                },
            )
        # 422 literal: 422 is deprecated in
        # newer starlette and the test runner turns the warning into an error.
        self.assertEqual(response.status_code, 422, response.text)

    def test_folio_and_invoice_mutually_exclusive_returns_422(self):
        folio = self._folio()
        invoice = self._invoice_for_folio(folio)
        with self._create_test_client(raise_server_exceptions=False) as test_client:
            self._login(test_client)
            response = test_client.post(
                "/payments",
                json={
                    "paymentType": "customerPayment",
                    "amount": 10.0,
                    "date": "2026-03-04",
                    "paymentMethodId": self.bank_inbound.id,
                    "folioId": folio.id,
                    "invoiceId": invoice.id,
                },
            )
        # 422 literal: 422 is deprecated in
        # newer starlette and the test runner turns the warning into an error.
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(response.json()["type"], "/errors/validation-error")

    def test_supplier_payment_without_partner_returns_422(self):
        with self._create_test_client(raise_server_exceptions=False) as test_client:
            self._login(test_client)
            response = test_client.post(
                "/payments",
                json={
                    "paymentType": "supplierPayment",
                    "amount": 80.0,
                    "date": "2026-03-04",
                    "paymentMethodId": self.bank_outbound.id,
                    "partnerId": None,
                },
            )
        # 422 literal: 422 is deprecated in
        # newer starlette and the test runner turns the warning into an error.
        self.assertEqual(response.status_code, 422, response.text)

    def test_unknown_payment_method_returns_404(self):
        with self._create_test_client(raise_server_exceptions=False) as test_client:
            self._login(test_client)
            response = test_client.post(
                "/payments",
                json={
                    "paymentType": "supplierPayment",
                    "amount": 80.0,
                    "date": "2026-03-04",
                    "paymentMethodId": 999999,
                    "partnerId": self.supplier.id,
                },
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
                "/payments",
                json={
                    "paymentType": "supplierPayment",
                    "amount": 40.0,
                    "date": "2026-03-04",
                    "paymentMethodId": self.cash_outbound.id,
                    "partnerId": self.supplier.id,
                },
            )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.text)
        self.assertEqual(len(Statement.search(domain)), 1)

    # -- POST /internal-transfers --

    def test_internal_transfer(self):
        """internalTransfer creates a transfer between two payment methods."""
        with self._create_test_client() as test_client:
            self._login(test_client)
            response = test_client.post(
                "/internal-transfers",
                json={
                    "amount": 30000.0,
                    "date": "2026-03-04",
                    "originPaymentMethodId": self.bank_outbound.id,
                    "destinationPaymentMethodId": self.bank2_inbound.id,
                    "reason": "Traspaso cierre de caja",
                },
            )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.text)
        body = response.json()
        self.assertEqual(body["paymentType"], "internalTransfer")
        self.assertEqual(body["amount"], 30000.0)
        self.assertIsNone(body["partner"])
        payment = self.env["account.payment"].browse(body["id"])
        self.assertTrue(payment.is_internal_transfer)
        self.assertEqual(payment.journal_id, self.journal_bank)
        self.assertEqual(payment.payment_method_line_id, self.bank_outbound)
        self.assertEqual(
            payment.paired_internal_transfer_payment_id.journal_id, self.journal_bank2
        )

    def test_internal_transfer_honors_destination_method_line(self):
        """When the destination journal exposes more than one inbound method
        line, the counterpart ends up with the one sent in the payload (not the
        default Odoo would pick)."""
        # Add a second inbound line to the destination journal and select it.
        manual = self.bank2_inbound.payment_method_id
        extra_inbound = self.env["account.payment.method.line"].create(
            {
                "name": "Bank PMS 2 inbound alt",
                "payment_method_id": manual.id,
                "journal_id": self.journal_bank2.id,
            }
        )
        self.assertNotEqual(extra_inbound, self.bank2_inbound)
        with self._create_test_client() as test_client:
            self._login(test_client)
            response = test_client.post(
                "/internal-transfers",
                json={
                    "amount": 500.0,
                    "date": "2026-03-04",
                    "originPaymentMethodId": self.bank_outbound.id,
                    "destinationPaymentMethodId": extra_inbound.id,
                    "reason": "",
                },
            )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.text)
        payment = self.env["account.payment"].browse(response.json()["id"])
        counterpart = payment.paired_internal_transfer_payment_id
        self.assertEqual(counterpart.payment_method_line_id, extra_inbound)
        self.assertEqual(counterpart.state, "posted")
        transfer_lines = (
            payment.move_id.line_ids + counterpart.move_id.line_ids
        ).filtered(lambda line: line.account_id == payment.destination_account_id)
        self.assertTrue(all(transfer_lines.mapped("reconciled")))

    def test_internal_transfer_same_journal_returns_422(self):
        with self._create_test_client(raise_server_exceptions=False) as test_client:
            self._login(test_client)
            response = test_client.post(
                "/internal-transfers",
                json={
                    "amount": 100.0,
                    "date": "2026-03-04",
                    "originPaymentMethodId": self.bank_outbound.id,
                    "destinationPaymentMethodId": self.bank_inbound.id,
                    "reason": "",
                },
            )
        # 422 literal: 422 is deprecated in
        # newer starlette and the test runner turns the warning into an error.
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(response.json()["type"], "/errors/validation-error")

    def test_internal_transfer_unknown_method_returns_404(self):
        with self._create_test_client(raise_server_exceptions=False) as test_client:
            self._login(test_client)
            response = test_client.post(
                "/internal-transfers",
                json={
                    "amount": 100.0,
                    "date": "2026-03-04",
                    "originPaymentMethodId": self.bank_outbound.id,
                    "destinationPaymentMethodId": 999999,
                    "reason": "",
                },
            )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND, response.text)
        self.assertEqual(response.json()["type"], "/errors/record-not-found")

    def test_internal_transfer_origin_not_outbound_returns_422(self):
        with self._create_test_client(raise_server_exceptions=False) as test_client:
            self._login(test_client)
            response = test_client.post(
                "/internal-transfers",
                json={
                    "amount": 100.0,
                    "date": "2026-03-04",
                    "originPaymentMethodId": self.bank_inbound.id,
                    "destinationPaymentMethodId": self.bank2_inbound.id,
                    "reason": "",
                },
            )
        # 422 literal: 422 is deprecated in
        # newer starlette and the test runner turns the warning into an error.
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(response.json()["type"], "/errors/validation-error")

    def test_internal_transfer_destination_not_inbound_returns_422(self):
        with self._create_test_client(raise_server_exceptions=False) as test_client:
            self._login(test_client)
            response = test_client.post(
                "/internal-transfers",
                json={
                    "amount": 100.0,
                    "date": "2026-03-04",
                    "originPaymentMethodId": self.bank_outbound.id,
                    "destinationPaymentMethodId": self.bank2_outbound.id,
                    "reason": "",
                },
            )
        # 422 literal: 422 is deprecated in
        # newer starlette and the test runner turns the warning into an error.
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(response.json()["type"], "/errors/validation-error")

    # -- PATCH /payments/{id} --

    def _create_supplier_payment(self, amount=80.0):
        return (
            self.env["account.payment"]
            .sudo()
            .create(
                {
                    "journal_id": self.journal_bank.id,
                    "payment_method_line_id": self.bank_outbound.id,
                    "partner_id": self.supplier.id,
                    "amount": amount,
                    "date": "2026-03-04",
                    "payment_type": "outbound",
                    "partner_type": "supplier",
                }
            )
        )

    def test_update_amount_and_date(self):
        """Partial edit of amount and date on a registered payment."""
        payment = self._create_supplier_payment()
        payment.action_post()
        with self._create_test_client() as test_client:
            self._login(test_client)
            response = test_client.patch(
                f"/payments/{payment.id}",
                json={"amount": 95.5, "date": "2026-04-10"},
            )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
        body = response.json()
        self.assertEqual(body["id"], payment.id)
        self.assertEqual(body["amount"], 95.5)
        self.assertEqual(body["date"], "2026-04-10")
        self.assertEqual(payment.state, "posted")
        self.assertEqual(payment.amount, 95.5)

    def test_update_payment_method_to_other_journal_recreates(self):
        """Odoo forbids changing the journal of a posted payment, so a payment
        method change that moves to another journal cancels the original and
        creates a replacement (new id), as the legacy API did."""
        payment = self._create_supplier_payment()
        payment.action_post()
        original_id = payment.id
        new_line = self.journal_bank2.outbound_payment_method_line_ids[:1]
        with self._create_test_client() as test_client:
            self._login(test_client)
            response = test_client.patch(
                f"/payments/{original_id}",
                json={"paymentMethodId": new_line.id},
            )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
        body = response.json()
        self.assertNotEqual(body["id"], original_id)
        self.assertEqual(body["paymentMethod"]["id"], new_line.id)
        new_payment = self.env["account.payment"].browse(body["id"])
        self.assertEqual(new_payment.journal_id, self.journal_bank2)
        self.assertEqual(new_payment.state, "posted")
        self.assertEqual(
            self.env["account.payment"].browse(original_id).state, "cancel"
        )

    def test_update_amount_not_positive_returns_422(self):
        payment = self._create_supplier_payment()
        payment.action_post()
        with self._create_test_client(raise_server_exceptions=False) as test_client:
            self._login(test_client)
            response = test_client.patch(
                f"/payments/{payment.id}",
                json={"amount": 0.0},
            )
        # 422 literal: 422 is deprecated in
        # newer starlette and the test runner turns the warning into an error.
        self.assertEqual(response.status_code, 422, response.text)

    def test_update_unknown_payment_returns_404(self):
        with self._create_test_client(raise_server_exceptions=False) as test_client:
            self._login(test_client)
            response = test_client.patch("/payments/999999", json={"amount": 10.0})
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND, response.text)
        self.assertEqual(response.json()["type"], "/errors/record-not-found")

    def test_update_unknown_payment_method_returns_404(self):
        payment = self._create_supplier_payment()
        payment.action_post()
        with self._create_test_client(raise_server_exceptions=False) as test_client:
            self._login(test_client)
            response = test_client.patch(
                f"/payments/{payment.id}",
                json={"paymentMethodId": 999999},
            )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND, response.text)
        self.assertEqual(response.json()["type"], "/errors/record-not-found")

    def test_update_internal_transfer_syncs_both_legs(self):
        """Editing the amount of one transfer leg updates the counterpart too
        and keeps the pair reconciled."""
        with self._create_test_client() as test_client:
            self._login(test_client)
            create = test_client.post(
                "/internal-transfers",
                json={
                    "amount": 30000.0,
                    "date": "2026-03-04",
                    "originPaymentMethodId": self.bank_outbound.id,
                    "destinationPaymentMethodId": self.bank2_inbound.id,
                    "reason": "Traspaso",
                },
            )
            self.assertEqual(create.status_code, status.HTTP_201_CREATED, create.text)
            payment = self.env["account.payment"].browse(create.json()["id"])
            counterpart = payment.paired_internal_transfer_payment_id
            self.assertTrue(counterpart)
            response = test_client.patch(
                f"/payments/{payment.id}",
                json={"amount": 25000.0},
            )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
        self.assertEqual(payment.amount, 25000.0)
        self.assertEqual(counterpart.amount, 25000.0)
        self.assertEqual(payment.state, "posted")
        self.assertEqual(counterpart.state, "posted")
        transfer_lines = (
            payment.move_id.line_ids + counterpart.move_id.line_ids
        ).filtered(lambda line: line.account_id == payment.destination_account_id)
        self.assertTrue(all(transfer_lines.mapped("reconciled")))

    def test_update_internal_transfer_payment_method_returns_422(self):
        with self._create_test_client(raise_server_exceptions=False) as test_client:
            self._login(test_client)
            create = test_client.post(
                "/internal-transfers",
                json={
                    "amount": 100.0,
                    "date": "2026-03-04",
                    "originPaymentMethodId": self.bank_outbound.id,
                    "destinationPaymentMethodId": self.bank2_inbound.id,
                    "reason": "",
                },
            )
            payment_id = create.json()["id"]
            response = test_client.patch(
                f"/payments/{payment_id}",
                json={"paymentMethodId": self.bank_inbound.id},
            )
        # 422 literal: 422 is deprecated in
        # newer starlette and the test runner turns the warning into an error.
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(response.json()["type"], "/errors/validation-error")
