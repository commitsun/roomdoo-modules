from odoo.exceptions import AccessError, UserError
from odoo.tests.common import TransactionCase, tagged

from .common import AdminToolsMixin, SupplierBillsMixin

PERIOD = {"date_from": "2026-09-01", "date_to": "2026-09-30"}


@tagged("post_install", "-at_install")
class TestAccPurchaseBillMismatch(
    AdminToolsMixin, SupplierBillsMixin, TransactionCase
):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_admin()
        if "purchase.order" not in cls.env:
            cls.skipTest(cls, "The purchase module is not installed.")
        cls._setup_bills()
        cls.product = cls.env["product.product"].create(
            {
                "name": "AI purchase service",
                "type": "service",
                "purchase_method": "purchase",
            }
        )
        cls.po_big = cls._order(1000, 1012.5, cls.prop)
        cls.po_equal = cls._order(500, 500, cls.prop)
        cls.po_tolerance = cls._order(300, 300.03, cls.prop)
        cls.po_foreign = cls._order(200, 250, cls.other_prop)
        cls.po_open = cls._order(100, 140, cls.prop, post=False)

    @classmethod
    def _order(cls, price, billed, prop, post=True):
        order = cls.env["purchase.order"].create(
            {
                "partner_id": cls.supplier_a.id,
                "order_line": [
                    (
                        0,
                        0,
                        {
                            "product_id": cls.product.id,
                            "name": "AI purchase line",
                            "product_qty": 1,
                            "price_unit": price,
                            "taxes_id": [(6, 0, [])],
                        },
                    )
                ],
            }
        )
        order.button_confirm()
        order.action_create_invoice()
        bill = order.invoice_ids
        bill.write({"invoice_date": "2026-09-10", "date": "2026-09-10"})
        bill.invoice_line_ids.write({"price_unit": billed, "tax_ids": [(6, 0, [])]})
        if post:
            bill.action_post()
        cls.env.cr.execute(
            "UPDATE account_move SET pms_property_id = %s WHERE id = %s",
            [prop.id, bill.id],
        )
        cls.env.invalidate_all()
        return order

    def _tool(self, **kwargs):
        return self._run("acc_purchase_bill_mismatch", **dict(PERIOD, **kwargs))

    def test_mismatch_above_tolerance(self):
        response = self._tool()
        self._assert_read_tool("acc_purchase_bill_mismatch", response)
        result = response["result"]
        rows = {row["order_id"]: row for row in result["mismatches"]}
        self.assertEqual(set(rows), {self.po_big.id})
        row = rows[self.po_big.id]
        self.assertEqual(row["order_total"], 1000.0)
        self.assertEqual(row["billed_total"], 1012.5)
        self.assertEqual(row["difference"], 12.5)
        self.assertEqual(row["property"], self.prop.name)
        self.assertEqual(row["supplier"], "AI Supplier A")
        self.assertEqual(result["totals"]["net_difference"], 12.5)
        # Draft bills are reported in the state summary, not compared.
        states = {(r["state"]): r for r in result["bills_by_state"]}
        self.assertEqual(states["draft"]["bills"], 1)

    def test_tolerance_and_scope(self):
        wide = self._tool(min_difference=0.01)["result"]["mismatches"]
        self.assertEqual(
            {row["order_id"] for row in wide}, {self.po_big.id, self.po_tolerance.id}
        )
        both = self._tool(user=self.reader_both)["result"]["mismatches"]
        self.assertEqual(
            {row["order_id"] for row in both}, {self.po_big.id, self.po_foreign.id}
        )
        with self.assertRaises(AccessError):
            self._tool(property_ids=[self.other_prop.id])
        with self.assertRaises(AccessError):
            self._tool(user=self.pms_only)
        with self.assertRaises(UserError):
            self._run(
                "acc_purchase_bill_mismatch",
                date_from="2026-01-01",
                date_to="2026-12-31",
            )
