from fastapi import status

from odoo import Command
from odoo.tests import tagged

from odoo.addons.pms_fastapi.tests.common import CommonTestPmsApi


@tagged("post_install", "-at_install")
class TestCashAndPaymentsFlow(CommonTestPmsApi):
    """End-to-end: open cash session -> create payment on the cash journal ->
    current breakdown -> close (with the shift payment reconciled)."""

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
        income_account = cls.env["account.account"].search(
            [("company_id", "=", company.id), ("account_type", "=", "income_other")],
            limit=1,
        ) or cls.env["account.account"].search(
            [("company_id", "=", company.id), ("account_type", "=", "income")], limit=1
        )
        expense_account = cls.env["account.account"].search(
            [("company_id", "=", company.id), ("account_type", "=", "expense")], limit=1
        )
        cls.journal_cash = cls.env["account.journal"].create(
            {
                "name": "Cash Reception",
                "type": "cash",
                "code": "CSHP",
                "company_id": company.id,
                "pms_property_ids": [Command.set([cls.test_property.id])],
                "profit_account_id": income_account.id,
                "loss_account_id": expense_account.id,
            }
        )
        cls.cash_inbound = cls.journal_cash.inbound_payment_method_line_ids[:1]
        cls.customer = cls.env["res.partner"].create({"name": "Flow Customer"})

    def _folio(self):
        return self.env["pms.folio"].create(
            {
                "pms_property_id": self.test_property.id,
                "partner_name": self.customer.name,
                "partner_id": self.customer.id,
            }
        )

    def test_open_pay_close_flow(self):
        folio = self._folio()
        with self._create_test_client() as test_client:
            self._login(test_client)

            # 1. Open the cash session with a declared base of 200.
            opened = test_client.post(
                "/cash-sessions",
                json={"journalId": self.journal_cash.id, "baseAmount": 200.0},
            )
            self.assertEqual(opened.status_code, status.HTTP_201_CREATED, opened.text)
            session_id = opened.json()["id"]
            self.assertEqual(opened.json()["expectedAmount"], 200.0)

            # 2. Register a customer payment of 120 on the cash journal.
            paid = test_client.post(
                "/payments",
                json={
                    "paymentType": "customerPayment",
                    "amount": 120.0,
                    "date": "2026-03-04",
                    "paymentMethodId": self.cash_inbound.id,
                    "folioId": folio.id,
                    "reference": "",
                },
            )
            self.assertEqual(paid.status_code, status.HTTP_201_CREATED, paid.text)
            payment_id = paid.json()["id"]

            # 3. current reflects the payment in the breakdown.
            current = test_client.get(
                f"/cash-sessions/current?journalId={self.journal_cash.id}"
            )
            self.assertEqual(current.status_code, status.HTTP_200_OK, current.text)
            self.assertEqual(current.json()["id"], session_id)
            self.assertEqual(current.json()["incomeAmount"], 120.0)
            self.assertEqual(current.json()["expectedAmount"], 320.0)

            # 4. Close counting exactly the expected cash -> no mismatch.
            closed = test_client.post(
                f"/cash-sessions/{session_id}/closing",
                json={"countedCash": 320.0, "note": "shift handover"},
            )
            self.assertEqual(closed.status_code, status.HTTP_200_OK, closed.text)
            body = closed.json()
            self.assertEqual(body["expectedAmount"], 320.0)
            self.assertEqual(body["countedCash"], 320.0)
            self.assertEqual(body["difference"], 0.0)
            self.assertEqual(body["closingAmount"], 320.0)

            # 5. last-closing returns it; current is now empty.
            last_closing = test_client.get(
                f"/cash-sessions/last-closing?journalId={self.journal_cash.id}"
            )
            self.assertEqual(
                last_closing.status_code, status.HTTP_200_OK, last_closing.text
            )
            self.assertEqual(last_closing.json()["closingAmount"], 320.0)
            after = test_client.get(
                f"/cash-sessions/current?journalId={self.journal_cash.id}"
            )
            self.assertEqual(after.status_code, status.HTTP_204_NO_CONTENT, after.text)

        # The close must have created the statement line for the shift payment
        # and reconciled it (the accounting close ran end-to-end).
        session = self.env["account.bank.statement"].browse(session_id)
        self.assertTrue(session.cash_session_closed)
        self.assertTrue(session.line_ids)
        self.assertTrue(self.env["account.payment"].browse(payment_id).is_matched)
