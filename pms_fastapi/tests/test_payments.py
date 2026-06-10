from datetime import date

from fastapi import status

from odoo import Command
from odoo.tests import tagged

from odoo.addons.pms_fastapi.tests.common import CommonTestPmsApi


@tagged("post_install", "-at_install")
class TestPaymentsEndpoints(CommonTestPmsApi):
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
        # Journals tied to the property: only these must be in scope.
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
        # Journal NOT tied to any property: must stay out of scope.
        cls.journal_no_property = cls.env["account.journal"].create(
            {
                "name": "Bank no property",
                "type": "bank",
                "code": "BNKNP",
                "company_id": company.id,
            }
        )
        cls.customer = cls.env["res.partner"].create({"name": "Payments Customer"})
        cls.supplier = cls.env["res.partner"].create({"name": "Payments Supplier"})

    def _create_payment(
        self,
        amount=100.0,
        payment_type="inbound",
        partner_type="customer",
        journal=None,
        partner=None,
        ref="",
        pay_date=None,
        is_internal_transfer=False,
        destination_journal=None,
        post=True,
    ):
        vals = {
            "amount": amount,
            "payment_type": payment_type,
            "partner_type": partner_type,
            "journal_id": (journal or self.journal_bank).id,
            "partner_id": (partner or self.customer).id,
            "ref": ref,
            "date": pay_date or date(2025, 12, 22),
            "is_internal_transfer": is_internal_transfer,
        }
        if is_internal_transfer:
            vals["destination_journal_id"] = (
                destination_journal or self.journal_bank2
            ).id
        payment = self.env["account.payment"].create(vals)
        if post:
            payment.action_post()
        return payment

    def _items_by_id(self, response):
        return {item["id"]: item for item in response.json()["items"]}

    def test_list_payments_shape_and_mapping(self):
        """GET /payments returns count+items and maps a customer payment."""
        payment = self._create_payment(amount=120.0, ref="206/26/026544")
        with self._create_test_client() as test_client:
            self._login(test_client)
            response = test_client.get(
                f"/payments?pmsPropertyId={self.test_property.id}"
            )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
        body = response.json()
        self.assertIn("count", body)
        self.assertIn("items", body)
        item = self._items_by_id(response)[payment.id]
        self.assertEqual(item["paymentType"], "customerPayment")
        self.assertEqual(item["amount"], 120.0)
        self.assertEqual(item["reference"], "206/26/026544")
        self.assertEqual(item["partner"]["id"], self.customer.id)
        self.assertIsNotNone(item["currency"]["code"])
        self.assertIsNotNone(item["createdBy"]["id"])
        self.assertIsNotNone(item["paymentMethod"]["id"])

    def test_amount_always_positive(self):
        """Outbound payments are reported with a positive (absolute) amount."""
        refund = self._create_payment(
            amount=50.0, payment_type="outbound", partner_type="customer"
        )
        with self._create_test_client() as test_client:
            self._login(test_client)
            response = test_client.get(
                f"/payments?pmsPropertyId={self.test_property.id}"
            )
        item = self._items_by_id(response)[refund.id]
        self.assertEqual(item["paymentType"], "customerRefund")
        self.assertEqual(item["amount"], 50.0)

    def test_payment_type_filter(self):
        """paymentType filters by transaction type."""
        customer_pay = self._create_payment(
            payment_type="inbound", partner_type="customer"
        )
        supplier_pay = self._create_payment(
            payment_type="outbound", partner_type="supplier", partner=self.supplier
        )
        with self._create_test_client() as test_client:
            self._login(test_client)
            response = test_client.get(
                f"/payments?pmsPropertyId={self.test_property.id}"
                "&paymentType=customerPayment"
            )
        items = self._items_by_id(response)
        self.assertIn(customer_pay.id, items)
        self.assertNotIn(supplier_pay.id, items)

    def test_amount_filters(self):
        """amountGt / amountLt / amountEq filter by absolute amount."""
        low = self._create_payment(amount=10.0)
        high = self._create_payment(amount=1000.0)
        with self._create_test_client() as test_client:
            self._login(test_client)
            gt = self._items_by_id(
                test_client.get(
                    f"/payments?pmsPropertyId={self.test_property.id}&amountGt=100"
                )
            )
            eq = self._items_by_id(
                test_client.get(
                    f"/payments?pmsPropertyId={self.test_property.id}&amountEq=10"
                )
            )
        self.assertIn(high.id, gt)
        self.assertNotIn(low.id, gt)
        self.assertIn(low.id, eq)
        self.assertNotIn(high.id, eq)

    def test_reference_filter(self):
        """reference filters exclusively on the payment reference."""
        match = self._create_payment(ref="UNIQUEREF123")
        other = self._create_payment(ref="somethingelse")
        with self._create_test_client() as test_client:
            self._login(test_client)
            items = self._items_by_id(
                test_client.get(
                    f"/payments?pmsPropertyId={self.test_property.id}"
                    "&reference=UNIQUEREF123"
                )
            )
        self.assertIn(match.id, items)
        self.assertNotIn(other.id, items)

    def test_invalid_date_range_returns_422(self):
        """dateTo earlier than dateFrom is rejected with 422."""
        with self._create_test_client(raise_server_exceptions=False) as test_client:
            self._login(test_client)
            response = test_client.get(
                f"/payments?pmsPropertyId={self.test_property.id}"
                "&dateFrom=2025-12-31&dateTo=2025-01-01"
            )
        self.assertEqual(
            response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY, response.text
        )

    def test_property_journal_scope(self):
        """Payments on journals not tied to the property are out of scope."""
        in_scope = self._create_payment(journal=self.journal_bank)
        out_scope = self._create_payment(journal=self.journal_no_property)
        with self._create_test_client() as test_client:
            self._login(test_client)
            items = self._items_by_id(
                test_client.get(f"/payments?pmsPropertyId={self.test_property.id}")
            )
        self.assertIn(in_scope.id, items)
        self.assertNotIn(out_scope.id, items)

    def test_internal_transfer_both_legs(self):
        """Internal transfers return BOTH legs (inbound + outbound), each as
        internalTransfer, replicating the legacy API (no dedup)."""
        outbound = self._create_payment(
            amount=30000.0,
            payment_type="outbound",
            is_internal_transfer=True,
            destination_journal=self.journal_bank2,
        )
        inbound = self._create_payment(
            amount=30000.0,
            payment_type="inbound",
            is_internal_transfer=True,
            journal=self.journal_bank2,
            destination_journal=self.journal_bank,
        )
        with self._create_test_client() as test_client:
            self._login(test_client)
            items = self._items_by_id(
                test_client.get(f"/payments?pmsPropertyId={self.test_property.id}")
            )
        self.assertIn(outbound.id, items)
        self.assertIn(inbound.id, items)
        self.assertEqual(items[outbound.id]["paymentType"], "internalTransfer")
        self.assertEqual(items[inbound.id]["paymentType"], "internalTransfer")
        # Both legs carry a positive amount (contract); they are told apart by
        # their payment method, not by sign.
        self.assertEqual(items[outbound.id]["amount"], 30000.0)
        self.assertEqual(items[inbound.id]["amount"], 30000.0)
