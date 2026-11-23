from odoo.exceptions import AccessError, UserError
from odoo.tests.common import TransactionCase, new_test_user, tagged

from odoo.addons.pms_ai_tool.tools import PARAM_WRITE_ENABLED

from .common import AdminToolsMixin, SupplierBillsMixin

TOOL = "acc_payment_order_prepare"
ORDER_MODEL = "account.payment.order"
CUTOFF = "2026-10-31"


@tagged("post_install", "-at_install")
class TestAccPaymentOrderTool(TransactionCase):
    """Checks that do not need the payment orders module."""

    def test_tool_record_is_a_write_tool(self):
        tool = self.env.ref(f"pms_ai_tool_admin.ai_tool_{TOOL}")
        self.assertEqual(tool.guard_mode, "write")
        annotations = tool._get_tool_definition()["annotations"]
        self.assertFalse(annotations["readOnlyHint"])
        self.assertTrue(tool.description.startswith("SENSITIVE"))

    def test_refused_without_the_module(self):
        if ORDER_MODEL in self.env:
            self.skipTest("account_payment_order is installed.")
        user = new_test_user(
            self.env,
            login="ai_payment_order_user",
            groups="base.group_user,account.group_account_user",
        )
        tool = self.env.ref(f"pms_ai_tool_admin.ai_tool_{TOOL}")
        tool.write({"allowed_user_ids": [(6, 0, user.ids)]})
        with self.assertRaisesRegex(UserError, "not installed"):
            tool.with_user(user)._execute_tool(payment_mode="Any", due_date=CUTOFF)


@tagged("post_install", "-at_install")
class TestAccPaymentOrderPrepare(AdminToolsMixin, SupplierBillsMixin, TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if ORDER_MODEL not in cls.env:
            cls.skipTest(cls, "account_payment_order is not installed.")
        cls._setup_admin()
        cls._setup_bills()
        group = cls.env.ref("account_payment_order.group_account_payment")
        (cls.reader | cls.reader_both).write({"groups_id": [(4, group.id)]})
        cls.tool = cls.env.ref(f"pms_ai_tool_admin.ai_tool_{TOOL}")
        cls.tool.write(
            {
                "allowed_user_ids": [
                    (6, 0, (cls.reader | cls.reader_both | cls.pms_only).ids)
                ],
                "write_enabled": True,
            }
        )
        cls.env["ir.config_parameter"].sudo().set_param(PARAM_WRITE_ENABLED, "True")
        company = cls.env.company
        cls.journal = cls.env["account.journal"].search(
            [("type", "=", "bank"), ("company_id", "=", company.id)], limit=1
        )
        cls.journal.bank_account_id = cls.env["res.partner.bank"].create(
            {"acc_number": "AI0000111122223333", "partner_id": company.partner_id.id}
        )
        method = cls.env.ref("account.account_payment_method_manual_out")
        method.bank_account_required = True
        cls.mode = cls.env["account.payment.mode"].create(
            {
                "name": "AI supplier transfer",
                "company_id": company.id,
                "bank_account_link": "fixed",
                "fixed_journal_id": cls.journal.id,
                "payment_method_id": method.id,
                "payment_order_ok": True,
                "default_payment_mode": "any",
            }
        )
        cls.env["res.partner.bank"].create(
            {"acc_number": "AI0000444455556666", "partner_id": cls.supplier_a.id}
        )
        cls.due = cls._bill(cls.supplier_a, 300, "2026-10-01", "2026-10-20", cls.prop)
        cls.later = cls._bill(cls.supplier_a, 120, "2026-10-01", "2027-01-20", cls.prop)
        cls.no_bank = cls._bill(
            cls.supplier_b, 80, "2026-10-01", "2026-10-20", cls.prop
        )
        cls.other = cls._bill(
            cls.supplier_a, 50, "2026-10-01", "2026-10-20", cls.other_prop
        )
        cls.draft = cls._bill(
            cls.supplier_a, 40, "2026-10-01", "2026-10-20", cls.prop, post=False
        )

    def _call(self, user=None, **kwargs):
        return self.tool.with_user(user or self.reader)._execute_tool(**kwargs)

    def _confirm(self, preview, user=None, **kwargs):
        token = preview["confirmation"]["confirmation_token"]
        return self._call(user=user, confirm=True, confirmation_token=token, **kwargs)

    def _new_order_args(self, **kwargs):
        return dict(payment_mode="AI supplier transfer", due_date=CUTOFF, **kwargs)

    def _orders(self):
        return self.env[ORDER_MODEL].search([("payment_mode_id", "=", self.mode.id)])

    def test_new_order_preview_changes_nothing_then_creates_it(self):
        lines_before = self.env["account.payment.line"].search_count([])
        preview = self._call(**self._new_order_args())
        self.assertFalse(self._orders())
        self.assertEqual(
            self.env["account.payment.line"].search_count([]), lines_before
        )
        result = preview["result"]
        bills = [row["bill_id"] for row in result["lines"]]
        self.assertEqual(bills, [self.due.id])
        self.assertEqual(result["lines"][0]["amount"], 300.0)
        self.assertEqual(result["lines"][0]["bank_account"], "****6666")
        reasons = {row["bill_id"]: row["reason"] for row in result["left_out"]}
        self.assertEqual(
            reasons, {self.later.id: "not_due", self.no_bank.id: "no_bank_account"}
        )
        self.assertTrue(result["order"]["new"])
        self.assertEqual(result["order"]["journal"], self.journal.name)
        self.assertEqual(result["totals"]["amount"], 300.0)

        applied = self._confirm(preview, **self._new_order_args())

        order = self._orders()
        self.assertEqual(len(order), 1)
        self.assertEqual(order.state, "draft")
        self.assertEqual(order.create_uid, self.reader)
        self.assertEqual(order.journal_id, self.journal)
        self.assertEqual(order.payment_line_ids.move_line_id.move_id, self.due)
        self.assertEqual(order.payment_line_ids.amount_currency, 300.0)
        self.assertEqual(applied["result"]["order"]["number"], order.name)
        self.assertEqual(applied["result"]["order"]["total"], 300.0)
        self.assertEqual(applied["result"]["added_lines"], 1)
        # Already in an order: a second order leaves it out.
        with self.assertRaisesRegex(UserError, "already_in_order"):
            self._call(**self._new_order_args())

    def test_confirm_order_never_generates_the_file(self):
        args = self._new_order_args(confirm_order=True)
        preview = self._call(**args)
        self.assertEqual(preview["result"]["order"]["state_after"], "open")
        applied = self._confirm(preview, **args)
        order = self._orders()
        self.assertEqual(order.state, "open")
        self.assertEqual(applied["result"]["order"]["state"], "open")
        self.assertFalse(
            self.env["ir.attachment"].search(
                [("res_model", "=", ORDER_MODEL), ("res_id", "=", order.id)]
            )
        )

    def test_add_bills_to_a_draft_order(self):
        order = self.env[ORDER_MODEL].create({"payment_mode_id": self.mode.id})
        args = {"order": order.name, "bill_ids": [self.due.id, self.no_bank.id]}
        preview = self._call(**args)
        self.assertFalse(order.payment_line_ids)
        self.assertEqual(
            [row["bill_id"] for row in preview["result"]["lines"]], [self.due.id]
        )
        self.assertEqual(preview["result"]["left_out"][0]["reason"], "no_bank_account")
        self._confirm(preview, **args)
        self.assertEqual(order.payment_line_ids.move_line_id.move_id, self.due)
        with self.assertRaisesRegex(UserError, "already_in_order"):
            self._call(order=str(order.id), bill_ids=[self.due.id])

    def test_token_is_bound_to_the_arguments(self):
        preview = self._call(**self._new_order_args())
        with self.assertRaisesRegex(UserError, "does not match"):
            self._confirm(
                preview, payment_mode="AI supplier transfer", due_date="2026-10-30"
            )
        self.assertFalse(self._orders())

    def test_refusals(self):
        order = self.env[ORDER_MODEL].create({"payment_mode_id": self.mode.id})
        with self.assertRaisesRegex(UserError, "not posted"):
            self._call(order=order.name, bill_ids=[self.draft.id])
        # A bill of a property the user cannot see.
        with self.assertRaisesRegex(UserError, "in your properties"):
            self._call(order=order.name, bill_ids=[self.other.id])
        with self.assertRaises(AccessError):
            self._call(user=self.pms_only, **self._new_order_args())
        with self.assertRaisesRegex(UserError, "give only bill_ids"):
            self._call(order=order.name, bill_ids=[self.due.id], due_date=CUTOFF)
        with self.assertRaisesRegex(UserError, "No outbound payment mode"):
            self._call(payment_mode="AI unknown mode", due_date=CUTOFF)
        order.write({"state": "cancel"})
        with self.assertRaisesRegex(UserError, "not in draft"):
            self._call(order=order.name, bill_ids=[self.due.id])
        self.assertFalse(order.payment_line_ids)
