from fastapi import status

from odoo import Command
from odoo.tests import tagged

from odoo.addons.pms_fastapi.tests.common import CommonTestPmsApi


@tagged("post_install", "-at_install")
class TestInvoicesEndpoints(CommonTestPmsApi):
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
        cls.journal_misc = cls.env["account.journal"].search(
            [("type", "=", "general"), ("company_id", "=", company.id)], limit=1
        )
        cls.receivable_account = cls.env["account.account"].search(
            [
                ("account_type", "=", "asset_receivable"),
                ("company_id", "=", company.id),
            ],
            limit=1,
        )
        cls.revenue_account = cls.env["account.account"].search(
            [("account_type", "=", "income"), ("company_id", "=", company.id)],
            limit=1,
        )
        cls.partner = cls.env["res.partner"].create({"name": "Test Invoice Partner"})

    def _create_invoice(self, move_type="out_invoice", amount=100.0):
        invoice = self.env["account.move"].create(
            {
                "move_type": move_type,
                "partner_id": self.partner.id,
                "pms_property_id": self.test_property.id,
                "journal_id": self.journal_sale.id,
                "invoice_line_ids": [
                    Command.create(
                        {
                            "name": "Test line",
                            "quantity": 1,
                            "price_unit": amount,
                            "tax_ids": [Command.clear()],
                        }
                    )
                ],
            }
        )
        invoice.action_post()
        return invoice

    def _find_item(self, response, move_id):
        return next(i for i in response.json()["items"] if i["id"] == move_id)

    def _reconcile_moves(self, *moves):
        lines = self.env["account.move.line"]
        for move in moves:
            lines |= move.line_ids.filtered(
                lambda line: line.account_type == "asset_receivable"
            )
        lines.reconcile()

    def test_invoices_get(self):
        with self._create_test_client() as test_client:
            self._login(test_client)
            response = test_client.get("/invoices")
            self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
            self.assertIn("count", response.json())
            self.assertIn("items", response.json())

    def test_invoice_payment_type_payment(self):
        """Register a payment against an invoice and check paymentType=payment."""
        invoice = self._create_invoice(amount=150.0)
        payment = (
            self.env["account.payment.register"]
            .with_context(active_model="account.move", active_ids=invoice.ids)
            .create({})
            ._create_payments()
        )
        with self._create_test_client() as test_client:
            self._login(test_client)
            response = test_client.get(f"/invoices?name={invoice.name}")
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            items = response.json()["items"]
            self.assertEqual(len(items), 1)
            inv_data = items[0]
            self.assertEqual(inv_data["paymentState"], "paid")
            self.assertEqual(len(inv_data["payments"]), 1)

            pay = inv_data["payments"][0]
            self.assertEqual(pay["paymentType"], "payment")
            self.assertEqual(pay["amount"], 150.0)
            self.assertEqual(pay["ref"], payment.move_id.name)
            self.assertEqual(pay["paymentDate"], str(payment.date))
            self.assertIsNotNone(pay["paymentMethod"])
            self.assertEqual(pay["journal"]["id"], payment.journal_id.id)

    def test_invoice_payment_type_refund(self):
        """Reconcile an invoice with a credit note and check paymentType=refund."""
        invoice = self._create_invoice(amount=200.0)
        refund = self._create_invoice(move_type="out_refund", amount=200.0)
        self._reconcile_moves(invoice, refund)

        with self._create_test_client() as test_client:
            self._login(test_client)
            response = test_client.get(f"/invoices?name={invoice.name}")
            inv_data = self._find_item(response, invoice.id)
            self.assertIn(inv_data["paymentState"], ("paid", "reversed"))
            self.assertEqual(len(inv_data["payments"]), 1)

            pay = inv_data["payments"][0]
            self.assertEqual(pay["paymentType"], "refund")
            self.assertEqual(pay["amount"], 200.0)
            self.assertEqual(pay["ref"], refund.name)
            self.assertEqual(pay["paymentDate"], str(refund.date))
            self.assertIsNone(pay["paymentMethod"])
            self.assertEqual(pay["journal"]["id"], refund.journal_id.id)

    def test_invoice_payment_type_entry(self):
        """Reconcile an invoice with a journal entry and check paymentType=entry."""
        invoice = self._create_invoice(amount=80.0)
        entry = self.env["account.move"].create(
            {
                "move_type": "entry",
                "journal_id": self.journal_misc.id,
                "line_ids": [
                    Command.create(
                        {
                            "account_id": self.receivable_account.id,
                            "partner_id": self.partner.id,
                            "credit": 80.0,
                        }
                    ),
                    Command.create(
                        {
                            "account_id": self.revenue_account.id,
                            "debit": 80.0,
                        }
                    ),
                ],
            }
        )
        entry.action_post()
        self._reconcile_moves(invoice, entry)

        with self._create_test_client() as test_client:
            self._login(test_client)
            response = test_client.get(f"/invoices?name={invoice.name}")
            inv_data = self._find_item(response, invoice.id)
            self.assertEqual(inv_data["paymentState"], "paid")
            self.assertEqual(len(inv_data["payments"]), 1)

            pay = inv_data["payments"][0]
            self.assertEqual(pay["paymentType"], "entry")
            self.assertEqual(pay["amount"], 80.0)
            self.assertEqual(pay["ref"], entry.name)
            self.assertEqual(pay["paymentDate"], str(entry.date))
            self.assertIsNone(pay["paymentMethod"])
            self.assertEqual(pay["journal"]["id"], entry.journal_id.id)

    def test_invoice_payment_type_invoice(self):
        """Reconcile a refund with an invoice and check paymentType=invoice
        from the refund's perspective."""
        invoice = self._create_invoice(amount=120.0)
        refund = self._create_invoice(move_type="out_refund", amount=120.0)
        self._reconcile_moves(invoice, refund)

        with self._create_test_client() as test_client:
            self._login(test_client)
            response = test_client.get(f"/invoices?name={refund.name}")
            inv_data = self._find_item(response, refund.id)
            self.assertEqual(len(inv_data["payments"]), 1)

            pay = inv_data["payments"][0]
            self.assertEqual(pay["paymentType"], "invoice")
            self.assertEqual(pay["amount"], 120.0)
            self.assertEqual(pay["ref"], invoice.name)
            self.assertEqual(pay["paymentDate"], str(invoice.date))

    def test_invoice_mixed_reconciliation(self):
        """Invoice partially paid with a payment and partially with a refund."""
        invoice = self._create_invoice(amount=300.0)

        # Partial payment of 200
        self.env["account.payment.register"].with_context(
            active_model="account.move", active_ids=invoice.ids
        ).create({"amount": 200.0})._create_payments()

        # Refund for remaining 100
        refund = self._create_invoice(move_type="out_refund", amount=100.0)
        self._reconcile_moves(invoice, refund)

        with self._create_test_client() as test_client:
            self._login(test_client)
            response = test_client.get(f"/invoices?name={invoice.name}")
            inv_data = self._find_item(response, invoice.id)
            self.assertEqual(inv_data["paymentState"], "paid")
            self.assertEqual(len(inv_data["payments"]), 2)

            payments_by_type = {p["paymentType"]: p for p in inv_data["payments"]}
            self.assertIn("payment", payments_by_type)
            self.assertIn("refund", payments_by_type)
            self.assertEqual(payments_by_type["payment"]["amount"], 200.0)
            self.assertEqual(payments_by_type["refund"]["amount"], 100.0)
            self.assertEqual(payments_by_type["refund"]["ref"], refund.name)
