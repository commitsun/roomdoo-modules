from fastapi import status

from odoo import Command
from odoo.tests import tagged

from odoo.addons.pms_fastapi.tests.common import CommonTestPmsApi


@tagged("post_install", "-at_install")
class TestInvoiceReconciliation(CommonTestPmsApi):
    """Payment reconciliation endpoints on /invoices/{id}.

    Covers create/delete reconciliation and the reconcilable-payments listing,
    including the RFC 9457 problem branches.
    """

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
        cls.journal_sale = cls.env["account.journal"].search(
            [("type", "=", "sale"), ("company_id", "=", company.id)], limit=1
        )
        cls.journal_bank = cls.env["account.journal"].search(
            [("type", "in", ("bank", "cash")), ("company_id", "=", company.id)],
            limit=1,
        )
        cls.partner = cls.env["res.partner"].create({"name": "Recon Customer"})
        cls.other_partner = cls.env["res.partner"].create({"name": "Other Customer"})

    # -- helpers -------------------------------------------------------
    def _create_invoice(self, amount=100.0, post=True):
        invoice = self.env["account.move"].create(
            {
                "move_type": "out_invoice",
                "partner_id": self.partner.id,
                "pms_property_id": self.test_property.id,
                "journal_id": self.journal_sale.id,
                "invoice_line_ids": [
                    Command.create(
                        {
                            "name": "Line",
                            "quantity": 1,
                            "price_unit": amount,
                            "tax_ids": [Command.clear()],
                        }
                    )
                ],
            }
        )
        if post:
            invoice.action_post()
        return invoice

    def _create_payment(self, amount=100.0, partner=None):
        payment = self.env["account.payment"].create(
            {
                "payment_type": "inbound",
                "partner_type": "customer",
                "partner_id": (partner or self.partner).id,
                "amount": amount,
                "journal_id": self.journal_bank.id,
            }
        )
        payment.action_post()
        return payment

    def _payment_id(self, payment):
        return f"payment_{payment.id}"

    # ------------------------------------------------------------------
    # POST /invoices/{id}/reconciliations
    # ------------------------------------------------------------------
    def test_create_reconciliation_invoice_not_found(self):
        payment = self._create_payment()
        with self._create_test_client() as test_client:
            self._login(test_client)
            response = test_client.post(
                "/invoices/999999999/reconciliations",
                json={"paymentId": self._payment_id(payment)},
            )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND, response.text)

    def test_create_reconciliation_draft_invoice_not_editable(self):
        invoice = self._create_invoice(post=False)
        payment = self._create_payment()
        with self._create_test_client() as test_client:
            self._login(test_client)
            response = test_client.post(
                f"/invoices/{invoice.id}/reconciliations",
                json={"paymentId": self._payment_id(payment)},
            )
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT, response.text)
        self.assertEqual(response.json()["type"], "/errors/invoice-not-editable")

    def test_create_reconciliation_payment_not_found(self):
        invoice = self._create_invoice()
        with self._create_test_client() as test_client:
            self._login(test_client)
            response = test_client.post(
                f"/invoices/{invoice.id}/reconciliations",
                json={"paymentId": "payment_999999999"},
            )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND, response.text)
        self.assertEqual(response.json()["type"], "/errors/payment-not-found")

    def test_create_reconciliation_payment_not_applicable(self):
        invoice = self._create_invoice()
        payment = self._create_payment(partner=self.other_partner)
        with self._create_test_client() as test_client:
            self._login(test_client)
            response = test_client.post(
                f"/invoices/{invoice.id}/reconciliations",
                json={"paymentId": self._payment_id(payment)},
            )
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT, response.text)
        self.assertEqual(response.json()["type"], "/errors/payment-not-applicable")

    def test_create_reconciliation_success(self):
        invoice = self._create_invoice(amount=100.0)
        payment = self._create_payment(amount=100.0)
        with self._create_test_client() as test_client:
            self._login(test_client)
            response = test_client.post(
                f"/invoices/{invoice.id}/reconciliations",
                json={"paymentId": self._payment_id(payment)},
            )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
        self.assertEqual(response.json()["paymentState"], "paid")

    def test_create_reconciliation_already_reconciled(self):
        invoice = self._create_invoice(amount=100.0)
        payment = self._create_payment(amount=40.0)
        with self._create_test_client() as test_client:
            self._login(test_client)
            first = test_client.post(
                f"/invoices/{invoice.id}/reconciliations",
                json={"paymentId": self._payment_id(payment)},
            )
            self.assertEqual(first.status_code, status.HTTP_200_OK, first.text)
            second = test_client.post(
                f"/invoices/{invoice.id}/reconciliations",
                json={"paymentId": self._payment_id(payment)},
            )
        self.assertEqual(second.status_code, status.HTTP_409_CONFLICT, second.text)
        self.assertEqual(second.json()["type"], "/errors/payment-already-reconciled")

    # ------------------------------------------------------------------
    # DELETE /invoices/{id}/reconciliations/{reconciliation_id}
    # ------------------------------------------------------------------
    def test_delete_reconciliation_invoice_not_found(self):
        with self._create_test_client() as test_client:
            self._login(test_client)
            response = test_client.delete(
                "/invoices/999999999/reconciliations/payment_1"
            )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND, response.text)

    def test_delete_reconciliation_not_found(self):
        invoice = self._create_invoice(amount=100.0)
        payment = self._create_payment(amount=100.0)
        with self._create_test_client() as test_client:
            self._login(test_client)
            response = test_client.delete(
                f"/invoices/{invoice.id}/reconciliations/{self._payment_id(payment)}"
            )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND, response.text)
        self.assertEqual(response.json()["type"], "/errors/reconciliation-not-found")

    def test_delete_reconciliation_success(self):
        invoice = self._create_invoice(amount=100.0)
        payment = self._create_payment(amount=40.0)
        with self._create_test_client() as test_client:
            self._login(test_client)
            created = test_client.post(
                f"/invoices/{invoice.id}/reconciliations",
                json={"paymentId": self._payment_id(payment)},
            )
            self.assertEqual(created.status_code, status.HTTP_200_OK, created.text)
            response = test_client.delete(
                f"/invoices/{invoice.id}/reconciliations/{self._payment_id(payment)}"
            )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
        self.assertEqual(response.json()["paymentState"], "notPaid")

    # ------------------------------------------------------------------
    # GET /invoices/{id}/reconcilable-payments
    # ------------------------------------------------------------------
    def test_reconcilable_payments_invoice_not_found(self):
        with self._create_test_client() as test_client:
            self._login(test_client)
            response = test_client.get("/invoices/999999999/reconcilable-payments")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND, response.text)

    def test_reconcilable_payments_draft_invoice_empty(self):
        invoice = self._create_invoice(post=False)
        with self._create_test_client() as test_client:
            self._login(test_client)
            response = test_client.get(f"/invoices/{invoice.id}/reconcilable-payments")
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
        self.assertEqual(response.json(), [])

    def test_reconcilable_payments_lists_available_payment(self):
        invoice = self._create_invoice(amount=100.0)
        payment = self._create_payment(amount=100.0)
        with self._create_test_client() as test_client:
            self._login(test_client)
            response = test_client.get(f"/invoices/{invoice.id}/reconcilable-payments")
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
        ids = [p["id"] for p in response.json()]
        self.assertIn(self._payment_id(payment), ids)
