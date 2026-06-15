from datetime import date

from fastapi import status

from odoo import Command
from odoo.tests import tagged

from odoo.addons.pms_fastapi.tests.common import CommonTestPmsApi


@tagged("post_install", "-at_install")
class TestCashSessionEndpoints(CommonTestPmsApi):
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
        cls.journal_bank = cls.env["account.journal"].create(
            {
                "name": "Bank PMS",
                "type": "bank",
                "code": "BNKP",
                "company_id": company.id,
                "pms_property_ids": [Command.set([cls.test_property.id])],
            }
        )
        cls.customer = cls.env["res.partner"].create({"name": "Cash Customer"})
        cls.supplier = cls.env["res.partner"].create({"name": "Cash Supplier"})

    def _create_payment(
        self,
        amount,
        payment_type="inbound",
        partner_type="customer",
        is_internal_transfer=False,
        destination_journal=None,
    ):
        vals = {
            "amount": amount,
            "payment_type": payment_type,
            "partner_type": partner_type,
            "journal_id": self.journal_cash.id,
            "partner_id": (
                self.customer if partner_type == "customer" else self.supplier
            ).id,
            "date": date(2025, 12, 22),
            "is_internal_transfer": is_internal_transfer,
        }
        if is_internal_transfer:
            vals["destination_journal_id"] = (
                destination_journal or self.journal_bank
            ).id
        payment = self.env["account.payment"].create(vals)
        payment.action_post()
        return payment

    def _open(self, test_client, base_amount=200.0, journal=None):
        return test_client.post(
            "/cash-sessions",
            json={
                "journalId": (journal or self.journal_cash).id,
                "baseAmount": base_amount,
            },
        )

    # -- open --

    def test_open_creates_session(self):
        """POST /cash-sessions opens a session; expected equals baseAmount."""
        with self._create_test_client() as test_client:
            self._login(test_client)
            response = self._open(test_client, base_amount=200.0)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.text)
        body = response.json()
        self.assertEqual(body["journal"]["id"], self.journal_cash.id)
        self.assertEqual(body["baseAmount"], 200.0)
        self.assertEqual(body["incomeAmount"], 0.0)
        self.assertEqual(body["expectedAmount"], 200.0)
        self.assertIsNotNone(body["openedBy"]["id"])
        self.assertTrue(body["currency"]["code"])

    def test_double_open_returns_409(self):
        """A second open on the same journal is rejected with 409."""
        with self._create_test_client(raise_server_exceptions=False) as test_client:
            self._login(test_client)
            self._open(test_client)
            response = self._open(test_client)
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT, response.text)
        self.assertEqual(response.json()["type"], "/errors/cash-session-already-open")

    def test_open_on_non_cash_journal_returns_409(self):
        """Opening a session on a non-cash journal returns 409."""
        with self._create_test_client(raise_server_exceptions=False) as test_client:
            self._login(test_client)
            response = self._open(test_client, journal=self.journal_bank)
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT, response.text)
        self.assertEqual(response.json()["type"], "/errors/journal-not-cash")

    # -- current / breakdown --

    def test_current_breakdown(self):
        """GET current returns the shift breakdown computed from payments.

        income = inbound non-transfer; refund = customer outbound;
        expense = supplier outbound; internalTransfer = net money leaving.
        """
        with self._create_test_client() as test_client:
            self._login(test_client)
            self._open(test_client, base_amount=200.0)
            self._create_payment(120.0, "inbound", "customer")  # income
            self._create_payment(20.0, "outbound", "customer")  # refund
            self._create_payment(50.0, "outbound", "supplier")  # expense
            self._create_payment(  # transfer OUT (lowers cash)
                30.0, "outbound", is_internal_transfer=True
            )
            self._create_payment(  # transfer IN (raises cash)
                10.0, "inbound", is_internal_transfer=True
            )
            response = test_client.get(
                f"/cash-sessions/current?journalId={self.journal_cash.id}"
            )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
        body = response.json()
        self.assertEqual(body["incomeAmount"], 120.0)
        self.assertEqual(body["refundAmount"], 20.0)
        self.assertEqual(body["expenseAmount"], 50.0)
        self.assertEqual(body["internalTransferAmount"], 20.0)  # 30 out - 10 in
        # expected = 200 + 120 - 20 - 50 - 20 = 230
        self.assertEqual(body["expectedAmount"], 230.0)

    def test_current_without_session_returns_204(self):
        """GET current returns 204 when there is no open session."""
        with self._create_test_client() as test_client:
            self._login(test_client)
            response = test_client.get(
                f"/cash-sessions/current?journalId={self.journal_cash.id}"
            )
        self.assertEqual(
            response.status_code, status.HTTP_204_NO_CONTENT, response.text
        )

    # -- last-closing --

    def test_last_closing_without_close_returns_204(self):
        """GET last-closing returns 204 when the journal never had a close."""
        with self._create_test_client() as test_client:
            self._login(test_client)
            response = test_client.get(
                f"/cash-sessions/last-closing?journalId={self.journal_cash.id}"
            )
        self.assertEqual(
            response.status_code, status.HTTP_204_NO_CONTENT, response.text
        )

    # -- close --

    def test_close_records_difference_and_transitions(self):
        """Closing records the mismatch, flips the session to closed, and
        last-closing then returns it while current becomes 204."""
        with self._create_test_client() as test_client:
            self._login(test_client)
            opened = self._open(test_client, base_amount=200.0).json()
            session_id = opened["id"]
            # No payments: expected == baseAmount == 200; count 180 -> -20 diff.
            close = test_client.post(
                f"/cash-sessions/{session_id}/closing",
                json={"countedCash": 180.0, "note": "short on cash"},
            )
            current_after = test_client.get(
                f"/cash-sessions/current?journalId={self.journal_cash.id}"
            )
            last_closing = test_client.get(
                f"/cash-sessions/last-closing?journalId={self.journal_cash.id}"
            )
        self.assertEqual(close.status_code, status.HTTP_200_OK, close.text)
        body = close.json()
        self.assertEqual(body["expectedAmount"], 200.0)
        self.assertEqual(body["countedCash"], 180.0)
        self.assertEqual(body["closingAmount"], 180.0)
        self.assertEqual(body["difference"], -20.0)
        self.assertEqual(body["note"], "short on cash")
        self.assertIsNotNone(body["closedBy"]["id"])
        self.assertEqual(
            current_after.status_code, status.HTTP_204_NO_CONTENT, current_after.text
        )
        self.assertEqual(
            last_closing.status_code, status.HTTP_200_OK, last_closing.text
        )
        self.assertEqual(last_closing.json()["closingAmount"], 180.0)
        self.assertEqual(last_closing.json()["note"], "short on cash")

    def test_close_missing_session_returns_404(self):
        """Closing a non-existent session returns 404."""
        with self._create_test_client(raise_server_exceptions=False) as test_client:
            self._login(test_client)
            response = test_client.post(
                "/cash-sessions/999999/closing",
                json={"countedCash": 0.0, "note": ""},
            )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND, response.text)

    def test_close_already_closed_returns_409(self):
        """Closing an already closed session returns 409."""
        with self._create_test_client(raise_server_exceptions=False) as test_client:
            self._login(test_client)
            session_id = self._open(test_client, base_amount=200.0).json()["id"]
            test_client.post(
                f"/cash-sessions/{session_id}/closing",
                json={"countedCash": 200.0, "note": ""},
            )
            response = test_client.post(
                f"/cash-sessions/{session_id}/closing",
                json={"countedCash": 200.0, "note": ""},
            )
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT, response.text)
        self.assertEqual(response.json()["type"], "/errors/cash-session-already-closed")
