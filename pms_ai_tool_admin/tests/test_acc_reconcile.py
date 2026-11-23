from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged

from odoo.addons.pms_ai_tool.tools import PARAM_WRITE_ENABLED

from .common import AdminToolsMixin

SUGGEST = "acc_reconcile_suggest"
RECONCILE = "acc_reconcile"
UNRECONCILE = "acc_unreconcile"
OPEN_TYPES = ("asset_receivable", "liability_payable")


class ReconcileFixtureMixin(AdminToolsMixin):
    """Customer invoices, a vendor bill and payments with fictitious data."""

    @classmethod
    def _setup_reconcile(cls, write_tools):
        cls._setup_admin()
        Partner = cls.env["res.partner"]
        cls.customer = Partner.create({"name": "AI Rec Customer", "is_company": True})
        cls.other_customer = Partner.create(
            {"name": "AI Rec Other Customer", "is_company": True}
        )
        company = cls.env.company
        cls.bank = cls.env["account.journal"].search(
            [("type", "=", "bank"), ("company_id", "=", company.id)], limit=1
        )
        cls.sale_journal = cls.env["account.journal"].search(
            [("type", "=", "sale"), ("company_id", "=", company.id)], limit=1
        )
        for tool in write_tools:
            cls.env.ref(f"pms_ai_tool_admin.ai_tool_{tool}").write(
                {
                    "allowed_user_ids": [(6, 0, (cls.reader | cls.reader_both).ids)],
                    "write_enabled": True,
                }
            )
        cls.env["ir.config_parameter"].sudo().set_param(PARAM_WRITE_ENABLED, "True")

    @classmethod
    def _move(cls, move_type, partner, price, prop, date="2026-10-01"):
        move = cls.env["account.move"].create(
            {
                "move_type": move_type,
                "partner_id": partner.id,
                "invoice_date": date,
                "date": date,
                "invoice_date_due": date,
                "invoice_payment_term_id": False,
                "invoice_line_ids": [
                    (
                        0,
                        0,
                        {
                            "name": "AI service",
                            "quantity": 1,
                            "price_unit": price,
                            "tax_ids": [(6, 0, [])],
                        },
                    )
                ],
            }
        )
        move.action_post()
        cls._set_property(move, prop)
        return move

    @classmethod
    def _set_property(cls, move, prop):
        cls.env.cr.execute(
            "UPDATE account_move SET pms_property_id = %s WHERE id = %s",
            [prop.id if prop else None, move.id],
        )
        cls.env.invalidate_all()

    @classmethod
    def _payment(cls, partner, value, outbound=False, date="2026-10-05"):
        payment = cls.env["account.payment"].create(
            {
                "payment_type": "outbound" if outbound else "inbound",
                "partner_type": "supplier" if outbound else "customer",
                "partner_id": partner.id,
                "amount": value,
                "journal_id": cls.bank.id,
                "date": date,
            }
        )
        payment.action_post()
        return payment

    @staticmethod
    def _line(record):
        move = record.move_id if record._name == "account.payment" else record
        return move.line_ids.filtered(
            lambda line: line.account_id.account_type in OPEN_TYPES
        )

    def _confirm(self, tool, preview, user=None, **kwargs):
        token = preview["confirmation"]["confirmation_token"]
        return self._run(
            tool, user=user, confirm=True, confirmation_token=token, **kwargs
        )

    def _assert_write_tool(self, tool):
        record = self.env.ref(f"pms_ai_tool_admin.ai_tool_{tool}")
        self.assertEqual(record.guard_mode, "write")
        annotations = record._get_tool_definition()["annotations"]
        self.assertFalse(annotations["readOnlyHint"])
        self.assertIn("Two steps: confirm=false previews", record.description)


@tagged("post_install", "-at_install")
class TestAccReconcile(ReconcileFixtureMixin, TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_reconcile([RECONCILE, UNRECONCILE])
        cls.inv100 = cls._move("out_invoice", cls.customer, 100, cls.prop)
        cls.inv50 = cls._move("out_invoice", cls.customer, 50, cls.prop)
        cls.inv30 = cls._move("out_invoice", cls.customer, 30, None)
        cls.foreign = cls._move("out_invoice", cls.customer, 70, cls.other_prop)
        cls.pay100 = cls._payment(cls.customer, 100)
        cls.pay80 = cls._payment(cls.customer, 80)
        cls.other_pay = cls._payment(cls.other_customer, 100)

    # acc_reconcile_suggest

    def test_suggest_by_partner(self):
        response = self._run(SUGGEST, partner=str(self.customer.id))
        self._assert_read_tool(SUGGEST, response)
        result = response["result"]
        self.assertEqual(result["partner"]["id"], self.customer.id)
        documents = {row["move_line_id"] for row in result["documents"]}
        credits = {row["move_line_id"] for row in result["credits"]}
        invoices = self._line(self.inv100) | self._line(self.inv50)
        self.assertEqual(documents, set((invoices | self._line(self.inv30)).ids))
        self.assertEqual(
            credits, set((self._line(self.pay100) | self._line(self.pay80)).ids)
        )
        self.assertEqual(
            [set(row["move_line_ids"]) for row in result["proposals"]],
            [
                set((self._line(self.inv100) | self._line(self.pay100)).ids),
                set(
                    (
                        self._line(self.inv50)
                        | self._line(self.inv30)
                        | self._line(self.pay80)
                    ).ids
                ),
            ],
        )
        self.assertEqual(result["proposals"][1]["amount"], 80.0)
        row = next(r for r in result["documents"] if r["number"] == self.inv100.name)
        self.assertEqual(row["kind"], "customer_invoice")
        self.assertEqual(row["residual"], 100.0)
        self.assertEqual(row["property"], "AI admin hotel")
        payment = next(r for r in result["credits"] if r["residual"] == -80.0)
        self.assertEqual(payment["kind"], "payment")

    def test_suggest_scope(self):
        foreign_line = self._line(self.foreign).id
        response = self._run(SUGGEST, partner=str(self.customer.id))
        lines = [row["move_line_id"] for row in response["result"]["documents"]]
        self.assertNotIn(foreign_line, lines)
        response = self._run(
            SUGGEST, user=self.reader_both, partner=str(self.customer.id)
        )
        lines = [row["move_line_id"] for row in response["result"]["documents"]]
        self.assertIn(foreign_line, lines)
        with self.assertRaisesRegex(UserError, "No posted entry"):
            self._run(SUGGEST, invoice=self.foreign.name)
        with self.assertRaisesRegex(UserError, "accounting access"):
            self.env.ref(f"pms_ai_tool_admin.ai_tool_{SUGGEST}").with_user(
                self.pms_only
            )._execute_tool(partner=str(self.customer.id))

    def test_suggest_by_invoice_and_vat(self):
        response = self._run(SUGGEST, invoice=self.inv100.name)
        result = response["result"]
        self.assertEqual(
            [set(row["move_line_ids"]) for row in result["proposals"]],
            [set((self._line(self.inv100) | self._line(self.pay100)).ids)],
        )
        self.assertEqual(
            [row["move_line_id"] for row in result["documents"]],
            self._line(self.inv100).ids,
        )
        self.assertEqual(len(result["credits"]), 2)
        self.env.cr.execute(
            "UPDATE res_partner SET vat = 'XX00000001A' WHERE id = %s",
            [self.customer.id],
        )
        self.env.invalidate_all()
        by_vat = self._run(SUGGEST, partner="00000001-A")
        self.assertEqual(by_vat["result"]["partner"]["id"], self.customer.id)
        # A VAT made only of digits is not taken for an id.
        self.env.cr.execute(
            "UPDATE res_partner SET vat = 'XX987654321' WHERE id = %s",
            [self.customer.id],
        )
        self.env.invalidate_all()
        by_vat = self._run(SUGGEST, partner="987654321")
        self.assertEqual(by_vat["result"]["partner"]["id"], self.customer.id)
        for kwargs in ({}, {"partner": "1", "invoice": "1"}):
            with self.assertRaisesRegex(UserError, "either a partner"):
                self._run(SUGGEST, **kwargs)
        with self.assertRaisesRegex(UserError, "No partner found"):
            self._run(SUGGEST, partner="ZZ99999999")

    # acc_reconcile

    def test_reconcile_preview_changes_nothing_then_apply(self):
        lines = self._line(self.inv100) | self._line(self.pay80)
        ids = lines.ids
        preview = self._run(RECONCILE, move_line_ids=ids)
        self.assertTrue(preview["confirmation"]["required"])
        self.assertFalse(lines.matched_debit_ids | lines.matched_credit_ids)
        self.assertEqual(self.inv100.amount_residual, 100.0)
        entries = {row["number"]: row for row in preview["result"]["entries"]}
        self.assertEqual(entries[self.inv100.name]["becomes"], "partially_paid")
        self.assertEqual(entries[self.inv100.name]["residual_after"], 20.0)
        self.assertEqual(entries[self.pay80.move_id.name]["becomes"], "paid")
        self.assertEqual(
            [row["move_line_id"] for row in preview["result"]["left_open"]],
            self._line(self.inv100).ids,
        )

        applied = self._confirm(RECONCILE, preview, move_line_ids=ids)

        self.assertEqual(self.inv100.amount_residual, 20.0)
        self.assertEqual(self.inv100.payment_state, "partial")
        self.assertTrue(self._line(self.pay80).reconciled)
        result = {row["number"]: row for row in applied["result"]["entries"]}
        self.assertEqual(result[self.inv100.name]["residual"], 20.0)
        self.assertEqual(result[self.inv100.name]["payment_state"], "partial")
        # The items are reconciled now: a second confirmation is refused.
        with self.assertRaises(UserError):
            self._confirm(RECONCILE, preview, move_line_ids=ids)

    def test_reconcile_exact_match_pays_the_invoice(self):
        ids = (self._line(self.inv100) | self._line(self.pay100)).ids
        preview = self._run(RECONCILE, move_line_ids=ids)
        self.assertEqual(preview["result"]["left_open"], [])
        self.assertEqual(
            {row["becomes"] for row in preview["result"]["entries"]}, {"paid"}
        )
        self._confirm(RECONCILE, preview, move_line_ids=ids)
        self.assertIn(self.inv100.payment_state, ("paid", "in_payment"))
        self.assertTrue(self._line(self.inv100).reconciled)
        # Already reconciled now.
        with self.assertRaisesRegex(UserError, "already reconciled"):
            self._run(RECONCILE, move_line_ids=ids)

    def test_reconcile_token_is_bound_to_the_items(self):
        ids = (self._line(self.inv100) | self._line(self.pay100)).ids
        preview = self._run(RECONCILE, move_line_ids=ids)
        other = (self._line(self.inv100) | self._line(self.pay80)).ids
        with self.assertRaisesRegex(UserError, "does not match"):
            self._confirm(RECONCILE, preview, move_line_ids=other)
        with self.assertRaisesRegex(UserError, "Missing confirmation_token"):
            self._run(RECONCILE, move_line_ids=ids, confirm=True)
        self.assertFalse(self._line(self.inv100).reconciled)

    def test_reconcile_refusals(self):
        inv = self._line(self.inv100).id
        bill = self._move("in_invoice", self.customer, 100, self.prop)
        cases = (
            ([inv, self._line(self.other_pay).id], "different partners"),
            ([self._line(bill).id, self._line(self.pay100).id], "different accounts"),
            ([inv, self._line(self.foreign).id], "not found in your companies"),
            ([inv, self._line(self.inv50).id], "Nothing to reconcile"),
            ([inv], "at least 2"),
            (list(range(1, 22)), "at most 20"),
            (
                [
                    inv,
                    self.inv100.line_ids.filtered(
                        lambda line: line.display_type == "product"
                    ).id,
                ],
                "not a receivable",
            ),
        )
        for ids, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(
                UserError, message
            ):
                self._run(RECONCILE, move_line_ids=ids)
        # The other hotel is in scope for a user of both hotels.
        preview = self._run(
            RECONCILE,
            user=self.reader_both,
            move_line_ids=[self._line(self.foreign).id, self._line(self.pay80).id],
        )
        self.assertEqual(len(preview["result"]["entries"]), 2)

    def test_reconcile_is_a_write_tool(self):
        self._assert_write_tool(RECONCILE)

    # acc_unreconcile

    def _reconciled_invoice(self):
        pay60 = self._payment(self.customer, 60)
        pay40 = self._payment(self.customer, 40)
        (self._line(self.inv100) | self._line(pay60) | self._line(pay40)).reconcile()
        self.assertIn(self.inv100.payment_state, ("paid", "in_payment"))
        return pay60, pay40

    def test_unreconcile_all(self):
        pay60, pay40 = self._reconciled_invoice()
        preview = self._run(UNRECONCILE, document=self.inv100.name)
        rows = preview["result"]["reconciliations"]
        self.assertEqual(
            {(row["counterpart"], row["amount"]) for row in rows},
            {(pay60.move_id.name, 60.0), (pay40.move_id.name, 40.0)},
        )
        self.assertEqual({row["counterpart_kind"] for row in rows}, {"payment"})
        self.assertEqual(preview["warnings"], [])
        # The preview changed nothing.
        self.assertTrue(self._line(self.inv100).reconciled)

        applied = self._confirm(UNRECONCILE, preview, document=self.inv100.name)

        self.assertEqual(
            sorted(applied["result"]["removed_partial_ids"]),
            sorted(row["partial_id"] for row in rows),
        )
        self.assertEqual(self.inv100.amount_residual, 100.0)
        self.assertEqual(self.inv100.payment_state, "not_paid")
        self.assertFalse(self._line(pay60).reconciled)
        self.assertEqual(pay60.move_id.line_ids.matched_debit_ids.ids, [])

    def test_unreconcile_selected(self):
        pay60, pay40 = self._reconciled_invoice()
        preview = self._run(UNRECONCILE, document=str(pay60.move_id.id))
        (row,) = preview["result"]["reconciliations"]
        self.assertEqual(row["counterpart"], self.inv100.name)
        preview = self._run(
            UNRECONCILE, document=self.inv100.name, partial_ids=[row["partial_id"]]
        )
        self.assertEqual(len(preview["result"]["reconciliations"]), 1)
        self._confirm(
            UNRECONCILE,
            preview,
            document=self.inv100.name,
            partial_ids=[row["partial_id"]],
        )
        self.assertEqual(self.inv100.amount_residual, 60.0)
        self.assertEqual(self.inv100.payment_state, "partial")
        self.assertTrue(self._line(pay40).reconciled)
        self.assertFalse(self._line(pay60).reconciled)

    def test_unreconcile_refusals(self):
        pay60, _pay40 = self._reconciled_invoice()
        with self.assertRaisesRegex(UserError, "are not of the entry"):
            self._run(UNRECONCILE, document=self.inv100.name, partial_ids=[1])
        with self.assertRaisesRegex(UserError, "has no reconciliations"):
            self._run(UNRECONCILE, document=self.inv50.name)
        with self.assertRaisesRegex(UserError, "No posted entry"):
            self._run(UNRECONCILE, document=self.foreign.name)
        with self.assertRaisesRegex(UserError, "at most 20"):
            self._run(
                UNRECONCILE, document=self.inv100.name, partial_ids=list(range(21))
            )
        # An empty selection is not "all of them".
        with self.assertRaisesRegex(UserError, "at least 1"):
            self._run(UNRECONCILE, document=self.inv100.name, partial_ids=[])
        # A counterpart in a hotel of other users cannot be touched.
        self._set_property(pay60.move_id, self.other_prop)
        with self.assertRaisesRegex(UserError, "outside your companies"):
            self._run(UNRECONCILE, document=self.inv100.name)
        self.assertTrue(self._line(self.inv100).reconciled)

    def test_unreconcile_warns_about_bank_statement_lines(self):
        statement_line = self.env["account.bank.statement.line"].create(
            {
                "journal_id": self.bank.id,
                "date": "2026-10-06",
                "payment_ref": "AI transfer",
                "partner_id": self.customer.id,
                "amount": 100.0,
            }
        )
        move = statement_line.move_id
        suspense = move.line_ids.filtered(
            lambda line: line.account_id == self.bank.suspense_account_id
        )
        move.button_draft()
        suspense.with_context(skip_account_move_synchronization=True).write(
            {"account_id": self._line(self.inv100).account_id.id}
        )
        move.action_post()
        (self._line(self.inv100) | suspense).reconcile()
        self.assertIn(self.inv100.payment_state, ("paid", "in_payment"))
        preview = self._run(UNRECONCILE, document=self.inv100.name)
        (row,) = preview["result"]["reconciliations"]
        self.assertEqual(row["counterpart_kind"], "bank_statement_line")
        self.assertTrue(row["from_bank_statement"])
        self.assertIn("pending to reconcile again", " ".join(preview["warnings"]))
        self._confirm(UNRECONCILE, preview, document=self.inv100.name)
        self.assertEqual(self.inv100.amount_residual, 100.0)
        self.assertFalse(suspense.reconciled)

    def test_unreconcile_is_a_write_tool(self):
        self._assert_write_tool(UNRECONCILE)
