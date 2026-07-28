from datetime import date

from fastapi import status

from odoo.tests import tagged

from odoo.addons.pms_fastapi.tests.common import CommonTestPmsApiPayment


@tagged("post_install", "-at_install")
class TestSupplierPaymentEndpoints(CommonTestPmsApiPayment):
    """POST/GET/PATCH /supplier-payments."""

    def _payment_body(self, **overrides):
        body = {
            "amount": 80.0,
            "date": "2026-03-04",
            "paymentMethodId": self.bank_outbound.id,
            "partnerId": self.supplier.id,
            "reference": "",
        }
        body.update(overrides)
        return body

    def _supplier_payment(self, amount=80.0, pay_date=None):
        return self._create_payment(
            amount=amount,
            payment_type="outbound",
            partner_type="supplier",
            partner=self.supplier,
            pay_date=pay_date,
        )

    # -- POST /supplier-payments --

    def test_create_payment(self):
        with self._create_test_client() as test_client:
            self._login(test_client)
            response = test_client.post(
                "/supplier-payments",
                json=self._payment_body(reference="206/26/026735"),
            )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.text)
        body = response.json()
        self.assertEqual(body["paymentType"], "supplierPayment")
        self.assertEqual(body["amount"], 80.0)
        self.assertEqual(body["partner"]["id"], self.supplier.id)
        self.assertEqual(body["reference"], "206/26/026735")
        self.assertEqual(body["bills"], [])
        payment = self.env["account.payment"].browse(body["id"])
        self.assertEqual(payment.state, "posted")
        self.assertEqual(payment.journal_id, self.journal_bank)
        self.assertEqual(payment.partner_type, "supplier")

    def test_create_without_partner_returns_422(self):
        """partnerId is required by the schema, not by a runtime check."""
        body = self._payment_body()
        del body["partnerId"]
        with self._create_test_client(raise_server_exceptions=False) as test_client:
            self._login(test_client)
            response = test_client.post("/supplier-payments", json=body)
        # 422 literal: 422 is deprecated in
        # newer starlette and the test runner turns the warning into an error.
        self.assertEqual(response.status_code, 422, response.text)

    def test_create_with_inbound_method_returns_422(self):
        """A supplier payment pays money out, so its method must be outbound."""
        with self._create_test_client(raise_server_exceptions=False) as test_client:
            self._login(test_client)
            response = test_client.post(
                "/supplier-payments",
                json=self._payment_body(paymentMethodId=self.bank_inbound.id),
            )
        # 422 literal: 422 is deprecated in
        # newer starlette and the test runner turns the warning into an error.
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(response.json()["type"], "/errors/validation-error")

    def test_create_unknown_payment_method_returns_404(self):
        with self._create_test_client(raise_server_exceptions=False) as test_client:
            self._login(test_client)
            response = test_client.post(
                "/supplier-payments", json=self._payment_body(paymentMethodId=999999)
            )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND, response.text)
        self.assertEqual(response.json()["type"], "/errors/record-not-found")

    def test_create_unknown_partner_returns_404(self):
        with self._create_test_client(raise_server_exceptions=False) as test_client:
            self._login(test_client)
            response = test_client.post(
                "/supplier-payments", json=self._payment_body(partnerId=999999)
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
                "/supplier-payments",
                json=self._payment_body(
                    amount=40.0, paymentMethodId=self.cash_outbound.id
                ),
            )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.text)
        self.assertEqual(len(Statement.search(domain)), 1)

    # -- GET /supplier-payments/{id} --

    def test_get_detail(self):
        payment = self._supplier_payment()
        with self._create_test_client() as test_client:
            self._login(test_client)
            response = test_client.get(f"/supplier-payments/{payment.id}")
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
        body = response.json()
        self.assertEqual(body["id"], payment.id)
        self.assertEqual(body["paymentType"], "supplierPayment")
        self.assertEqual(body["partner"]["id"], self.supplier.id)
        self.assertEqual(body["bills"], [])
        # A supplier payment settles bills, never folios or refunds.
        self.assertNotIn("folio", body)
        self.assertNotIn("availableRefundAmount", body)
        self.assertNotIn("invoices", body)

    def test_get_customer_payment_returns_404(self):
        """The entity only addresses supplier payments."""
        customer_pay = self._create_payment(amount=100.0)
        with self._create_test_client(raise_server_exceptions=False) as test_client:
            self._login(test_client)
            response = test_client.get(f"/supplier-payments/{customer_pay.id}")
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
            response = test_client.get(f"/supplier-payments/{transfer.id}")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND, response.text)

    # -- PATCH /supplier-payments/{id} --

    def test_update_amount_and_date(self):
        # Same accounting year as the new date: moving a posted entry across
        # years clashes with Odoo's entry-number sequence, unrelated to the API.
        payment = self._supplier_payment(pay_date=date(2026, 3, 4))
        with self._create_test_client() as test_client:
            self._login(test_client)
            response = test_client.patch(
                f"/supplier-payments/{payment.id}",
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
        payment = self._supplier_payment()
        other_supplier = self.env["res.partner"].create({"name": "Other Supplier"})
        with self._create_test_client() as test_client:
            self._login(test_client)
            response = test_client.patch(
                f"/supplier-payments/{payment.id}",
                json={"partnerId": other_supplier.id, "reference": "F-2026/1"},
            )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
        body = response.json()
        self.assertEqual(body["partner"]["id"], other_supplier.id)
        self.assertEqual(body["reference"], "F-2026/1")
        self.assertEqual(payment.partner_id, other_supplier)

    def test_update_keeps_the_same_id(self):
        """A PATCH never re-registers the payment, so the id is stable."""
        payment = self._supplier_payment()
        with self._create_test_client() as test_client:
            self._login(test_client)
            response = test_client.patch(
                f"/supplier-payments/{payment.id}", json={"amount": 90.0}
            )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
        self.assertEqual(response.json()["id"], payment.id)
        self.assertEqual(payment.state, "posted")
        self.assertEqual(payment.journal_id, self.journal_bank)

    def test_update_payment_method_is_not_expressible(self):
        """Every outbound account offers a single method, so correcting it always
        means moving accounts: a cancellation plus a new payment, not a PATCH."""
        payment = self._supplier_payment()
        with self._create_test_client(raise_server_exceptions=False) as test_client:
            self._login(test_client)
            response = test_client.patch(
                f"/supplier-payments/{payment.id}",
                json={"paymentMethodId": self.bank2_outbound.id},
            )
        # 422 literal: 422 is deprecated in
        # newer starlette and the test runner turns the warning into an error.
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(payment.journal_id, self.journal_bank)
        self.assertEqual(payment.state, "posted")

    def test_update_unknown_payment_returns_404(self):
        with self._create_test_client(raise_server_exceptions=False) as test_client:
            self._login(test_client)
            response = test_client.patch(
                "/supplier-payments/999999", json={"amount": 10.0}
            )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND, response.text)
        self.assertEqual(response.json()["type"], "/errors/payment-not-found")

    def test_update_customer_payment_returns_404(self):
        """A customer payment cannot be edited through this entity."""
        customer_pay = self._create_payment(amount=100.0)
        with self._create_test_client(raise_server_exceptions=False) as test_client:
            self._login(test_client)
            response = test_client.patch(
                f"/supplier-payments/{customer_pay.id}", json={"amount": 90.0}
            )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND, response.text)
        self.assertEqual(customer_pay.amount, 100.0)
