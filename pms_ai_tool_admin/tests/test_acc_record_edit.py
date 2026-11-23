from datetime import date

from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, new_test_user, tagged
from odoo.tools.mail import html2plaintext

from odoo.addons.pms_ai_tool.tools import PARAM_WRITE_ENABLED, get_guard

from .common import AdminToolsMixin, SupplierBillsMixin

TOOL = "acc_record_edit"


@tagged("post_install", "-at_install")
class TestAccRecordEdit(AdminToolsMixin, SupplierBillsMixin, TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_admin()
        cls._setup_bills()
        cls.editor = new_test_user(
            cls.env,
            login="ai_record_editor",
            groups="base.group_user,base.group_partner_manager,"
            "pms.group_pms_user,account.group_account_user",
            pms_property_ids=[(6, 0, cls.prop.ids)],
            pms_property_id=cls.prop.id,
        )
        cls.draft = cls._bill(
            cls.supplier_a, 100, "2026-10-01", "2026-10-31", cls.prop, post=False
        )
        cls.draft2 = cls._bill(
            cls.supplier_b, 200, "2026-10-02", "2026-11-01", cls.prop, post=False
        )
        cls.posted = cls._bill(
            cls.supplier_a, 300, "2026-10-03", "2026-11-02", cls.prop
        )
        cls.other_company = cls.env["res.company"].create({"name": "AI other company"})
        cls.tool = cls.env.ref(f"pms_ai_tool_admin.ai_tool_{TOOL}")
        cls.tool.write(
            {"allowed_user_ids": [(6, 0, cls.editor.ids)], "write_enabled": True}
        )
        cls.env["ir.config_parameter"].sudo().set_param(PARAM_WRITE_ENABLED, "True")

    def _call(self, user=None, **kwargs):
        return self.tool.with_user(user or self.editor)._execute_tool(**kwargs)

    def _confirm(self, preview, **kwargs):
        token = preview["confirmation"]["confirmation_token"]
        return self._call(confirm=True, confirmation_token=token, **kwargs)

    def _move_edit(self, records, fields, values):
        return {
            "model": "account.move",
            "record_ids": records.ids,
            "fields": fields,
            "values": values,
        }

    def test_preview_changes_nothing_and_apply_matches_it(self):
        moves = self.draft | self.draft2
        kwargs = self._move_edit(
            moves,
            ["ref", "invoice_date", "to_check", "narration"],
            ["AI-NEW-REF", "2026-10-05", "true", "Checked with the supplier"],
        )
        preview = self._call(**kwargs)
        moves.invalidate_recordset()
        self.assertEqual(self.draft.ref, f"AI-{self.supplier_a.id}-100")
        self.assertEqual(self.draft.invoice_date, date(2026, 10, 1))
        self.assertFalse(self.draft.to_check)
        self.assertTrue(preview["confirmation"]["required"])
        result = preview["result"]
        self.assertEqual((result["model"], result["to_change"]), ("account.move", 2))
        row = result["records"][0]
        self.assertEqual(row["id"], self.draft.id)
        changes = {change["field"]: change for change in row["fields"]}
        self.assertEqual(
            (changes["ref"]["old"], changes["ref"]["new"]),
            (f"AI-{self.supplier_a.id}-100", "AI-NEW-REF"),
        )
        self.assertEqual(
            (changes["invoice_date"]["old"], changes["invoice_date"]["new"]),
            ("2026-10-01", "2026-10-05"),
        )
        self.assertEqual(
            (changes["to_check"]["old"], changes["to_check"]["new"]), (False, True)
        )
        self.assertEqual(changes["narration"]["new"], "Checked with the supplier")
        self.assertTrue(all(change["changes"] for change in row["fields"]))
        # The bill date recomputes the accounting date: warned in the preview.
        self.assertTrue(preview["warnings"])

        applied = self._confirm(preview, **kwargs)
        moves.invalidate_recordset()
        for move in moves:
            self.assertEqual(move.ref, "AI-NEW-REF")
            self.assertEqual(move.invoice_date, date(2026, 10, 5))
            self.assertTrue(move.to_check)
            self.assertEqual(
                html2plaintext(move.narration), "Checked with the supplier"
            )
            self.assertEqual(move.state, "draft")
            # Written as the user, not as superuser.
            self.assertEqual(move.write_uid, self.editor)
        rows = {row["id"]: row for row in applied["result"]["records"]}
        self.assertEqual(
            rows[self.draft.id]["changed"],
            {
                "ref": "AI-NEW-REF",
                "invoice_date": "2026-10-05",
                "to_check": True,
                "narration": "Checked with the supplier",
            },
        )
        # A replay is refused: the values are already there.
        with self.assertRaisesRegex(UserError, "Nothing to change"):
            self._confirm(preview, **kwargs)

    def test_only_changed_fields_are_written(self):
        kwargs = self._move_edit(
            self.draft | self.draft2, ["partner_id"], [str(self.supplier_a.id)]
        )
        preview = self._call(**kwargs)
        self.assertEqual(preview["result"]["to_change"], 1)
        rows = {row["id"]: row for row in preview["result"]["records"]}
        self.assertFalse(rows[self.draft.id]["will_change"])
        self.assertTrue(rows[self.draft2.id]["will_change"])
        self._confirm(preview, **kwargs)
        self.assertEqual(self.draft2.partner_id, self.supplier_a)
        with self.assertRaisesRegex(UserError, "Nothing to change"):
            self._call(**kwargs)

    def test_many2one_by_id_or_exact_name(self):
        by_name = self._call(
            **self._move_edit(self.draft, ["partner_id"], ["AI Supplier B"])
        )
        new = by_name["result"]["records"][0]["fields"][0]["new"]
        self.assertEqual(new["id"], self.supplier_b.id)
        by_id = self._call(
            **self._move_edit(self.draft, ["partner_id"], [str(self.supplier_b.id)])
        )
        self.assertEqual(
            by_id["result"]["records"][0]["fields"][0]["new"]["id"], self.supplier_b.id
        )
        self.env["res.partner"].create([{"name": "AI Twin"}, {"name": "AI Twin"}])
        with self.assertRaisesRegex(UserError, "Several"):
            self._call(**self._move_edit(self.draft, ["partner_id"], ["AI Twin"]))
        with self.assertRaisesRegex(UserError, "No res.partner record found"):
            self._call(**self._move_edit(self.draft, ["partner_id"], ["AI Nobody"]))
        spain = self.env.ref("base.es")
        kwargs = {
            "model": "res.partner",
            "record_ids": self.supplier_a.ids,
            "fields": ["country_id", "city", "email"],
            "values": [spain.name, "AI City", "ai.supplier@example.com"],
        }
        self._confirm(self._call(**kwargs), **kwargs)
        self.assertEqual(self.supplier_a.country_id, spain)
        self.assertEqual(self.supplier_a.city, "AI City")

    def test_type_conversions(self):
        tools = self.env["pms.ai.tools"].with_user(self.editor)
        Move = self.env["account.move"]
        convert = tools._ai_edit_convert
        self.assertEqual(
            convert(Move._fields["date"], " 2026-10-09 "), date(2026, 10, 9)
        )
        self.assertIs(convert(Move._fields["to_check"], "Yes"), True)
        self.assertIs(convert(Move._fields["to_check"], "0"), False)
        self.assertEqual(convert(Move._fields["ref"], " AI ref "), "AI ref")
        self.assertIs(convert(Move._fields["ref"], "  "), False)
        quantity = self.env["account.move.line"]._fields["quantity"]
        self.assertEqual(convert(quantity, "12.50"), 12.5)
        with self.assertRaises(UserError):
            convert(quantity, "12,50")
        self.assertEqual(convert(Move._fields["move_type"], "in_invoice"), "in_invoice")
        for field, text in (
            ("date", "2026-13-01"),
            ("date", ""),
            ("to_check", "maybe"),
            ("move_type", "bill"),
        ):
            with self.subTest(field=field, text=text), self.assertRaises(UserError):
                convert(Move._fields[field], text)
        with self.assertRaisesRegex(UserError, "cannot be emptied"):
            convert(self.env["res.partner"]._fields["name"], "")
        # A text normalised by Odoo on write still matches the plan.
        vat = self.env["res.partner"]._fields["vat"]
        self.assertTrue(tools._ai_edit_matches(vat, "ESA1234", "es a-12.34"))
        self.assertFalse(tools._ai_edit_matches(vat, "ESA1234", "ESA1235"))
        self.assertFalse(
            tools._ai_edit_matches(Move._fields["date"], "2026-10-01", "2026-10-02")
        )
        # An empty text empties an optional field.
        kwargs = self._move_edit(self.draft, ["ref"], [""])
        preview = self._call(**kwargs)
        self.assertIsNone(preview["result"]["records"][0]["fields"][0]["new"])
        self._confirm(preview, **kwargs)
        self.assertFalse(self.draft.ref)

    def test_refusals(self):
        cases = [
            (self._move_edit(self.draft, ["amount_total"], ["1"]), "cannot be changed"),
            (self._move_edit(self.draft, ["state"], ["posted"]), "cannot be changed"),
            (self._move_edit(self.posted, ["ref"], ["X"]), "not in draft"),
            (
                self._move_edit(self.draft, ["ref", "ref"], ["X", "Y"]),
                "only once",
            ),
            (self._move_edit(self.draft, ["ref"], ["X", "Y"]), "same length"),
            (
                dict(self._move_edit(self.draft, ["ref"], ["X"]), model="res.users"),
                "must be one of",
            ),
            (
                dict(
                    self._move_edit(self.draft, ["ref"], ["X"]),
                    record_ids=list(range(1, 22)),
                ),
                "at most 20",
            ),
            (
                dict(self._move_edit(self.draft, ["ref"], ["X"]), operation="unlink"),
                "Unknown arguments",
            ),
            (
                {
                    "model": "res.partner",
                    "record_ids": self.editor.partner_id.ids,
                    "fields": ["email"],
                    "values": ["x@example.com"],
                },
                "partner of a user",
            ),
            (
                {
                    "model": "res.partner",
                    "record_ids": self.env.company.partner_id.ids,
                    "fields": ["vat"],
                    "values": ["X"],
                },
                "partner of a company",
            ),
        ]
        for kwargs, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(
                UserError, message
            ):
                self._call(**kwargs)
        self.assertFalse(self.draft.to_check)

    def test_records_out_of_scope(self):
        foreign = self.env["res.partner"].create(
            {"name": "AI foreign supplier", "company_id": self.other_company.id}
        )
        other_property = self._bill(
            self.supplier_a,
            400,
            "2026-10-04",
            "2026-11-03",
            self.other_prop,
            post=False,
        )
        for model, record, field in (
            ("res.partner", foreign, "city"),
            ("account.move", other_property, "ref"),
        ):
            with self.subTest(model=model), self.assertRaises(UserError):
                self._call(
                    model=model, record_ids=record.ids, fields=[field], values=["X"]
                )
        self.assertFalse(foreign.city)
        self.assertEqual(other_property.ref, f"AI-{self.supplier_a.id}-400")

    def test_state_and_data_are_checked_again_on_confirmation(self):
        kwargs = self._move_edit(self.draft, ["ref"], ["AI-LATE"])
        preview = self._call(**kwargs)
        self.draft.ref = "AI-CHANGED-MEANWHILE"
        with self.assertRaisesRegex(UserError, "does not match"):
            self._confirm(preview, **kwargs)
        self.assertEqual(self.draft.ref, "AI-CHANGED-MEANWHILE")
        preview = self._call(**kwargs)
        self.draft.action_post()
        with self.assertRaisesRegex(UserError, "not in draft"):
            self._confirm(preview, **kwargs)
        self.assertEqual(self.draft.ref, "AI-CHANGED-MEANWHILE")

    def test_payment_and_bank_account(self):
        journal = self.env["account.journal"].search(
            [("type", "=", "bank"), ("company_id", "=", self.env.company.id)], limit=1
        )
        payment = self.env["account.payment"].create(
            {
                "payment_type": "outbound",
                "partner_type": "supplier",
                "partner_id": self.supplier_a.id,
                "amount": 10,
                "journal_id": journal.id,
                "date": "2026-10-10",
            }
        )
        kwargs = {
            "model": "account.payment",
            "record_ids": payment.ids,
            "fields": ["ref", "date"],
            "values": ["AI transfer", "2026-10-12"],
        }
        self._confirm(self._call(**kwargs), **kwargs)
        self.assertEqual(payment.ref, "AI transfer")
        self.assertEqual(payment.date, date(2026, 10, 12))
        bank = self.env["res.partner.bank"].create(
            {"acc_number": "AI-ACC-1", "partner_id": self.supplier_b.id}
        )
        kwargs = {
            "model": "res.partner.bank",
            "record_ids": bank.ids,
            "fields": ["acc_number"],
            "values": ["AI-ACC-2"],
        }
        self._confirm(self._call(**kwargs), **kwargs)
        self.assertEqual(bank.acc_number, "AI-ACC-2")
        payment.action_post()
        with self.assertRaisesRegex(UserError, "not in draft"):
            self._call(
                model="account.payment",
                record_ids=payment.ids,
                fields=["ref"],
                values=["X"],
            )

    def test_it_is_a_write_tool(self):
        guard = get_guard(self.env["pms.ai.tools"]._ai_acc_record_edit)
        self.assertTrue(guard["write"])
        self.assertFalse(guard["log_result"])
        self.assertEqual(guard["apply"], "_apply_acc_record_edit")
        self.assertEqual(self.tool.guard_mode, "write")
        self.assertTrue(
            self.tool.description.startswith("SENSITIVE (accounting data).")
        )
        self.assertTrue(
            self.tool.description.endswith(
                "Two steps: confirm=false previews; only after the user's approval, "
                "confirm=true with the token."
            )
        )
        annotations = self.tool._get_tool_definition()["annotations"]
        self.assertTrue(annotations["destructiveHint"])
        # Write tools need an explicit list of users.
        with self.assertRaises(UserError):
            self._call(user=self.reader, **self._move_edit(self.draft, ["ref"], ["X"]))

    def test_payment_terms_kept_per_company(self):
        term = self.env["account.payment.term"].create(
            {"name": "AI 45 days", "company_id": self.env.company.id}
        )
        kwargs = {
            "model": "res.partner",
            "record_ids": self.supplier_a.ids,
            "fields": ["property_supplier_payment_term_id"],
            "values": ["AI 45 days"],
        }
        preview = self._call(**kwargs)
        self.assertIn("kept per company", " ".join(preview["warnings"]))
        self._confirm(preview, **kwargs)
        self.assertEqual(
            self.supplier_a.with_company(
                self.env.company
            ).property_supplier_payment_term_id,
            term,
        )
        foreign_term = self.env["account.payment.term"].create(
            {"name": "AI foreign term", "company_id": self.other_company.id}
        )
        field = self.env["res.partner"]._fields["property_supplier_payment_term_id"]
        with self.assertRaisesRegex(UserError, "another company"):
            self.env["pms.ai.tools"]._ai_edit_target(field, str(foreign_term.id))
