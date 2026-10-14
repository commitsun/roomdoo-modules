from datetime import date, timedelta

from freezegun import freeze_time

from odoo.exceptions import AccessError, UserError
from odoo.tests.common import TransactionCase
from odoo.tools import mute_logger

from ..tools import PARAM_WRITE_ENABLED, guarded_tool
from .common import PmsAiToolTestMixin

TOOLS_LOGGER = "odoo.addons.pms_ai_tool.tools"
SQL_LOGGER = "odoo.sql_db"


class TestGuardedTool(PmsAiToolTestMixin, TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_ai_tools()

    def _db_ref(self):
        self.env.cr.execute(
            "SELECT ref FROM res_partner WHERE id = %s", [self.partner.id]
        )
        return self.env.cr.fetchone()[0]

    def _show(self, setting):
        self.env.cr.execute(f"SHOW {setting}")
        return self.env.cr.fetchone()[0]

    # Read tools

    def test_read_tool_returns_the_envelope(self):
        response = self._run(
            "echo", self.user_a, date_from="2026-09-21", kind="a", property_ids=[1]
        )
        self.assertEqual(response["environment"], "test")
        self.assertEqual(response["tool"], "echo")
        self.assertEqual(response["summary"], "Echo")
        self.assertEqual(response["period"], {"date_from": "2026-09-21"})
        self.assertEqual(response["definitions"], ["Echoes its arguments."])
        self.assertEqual(response["warnings"], ["Test tool."])
        self.assertTrue(response["generated_at"])
        self.assertEqual(
            response["result"],
            {"echo": {"date_from": "2026-09-21", "kind": "a", "property_ids": [1]}},
        )

    def test_read_tool_validates_its_arguments(self):
        bad_calls = [
            {"date_from": "2026-09-21", "domain": "[]"},
            {},
            {"date_from": "21/09/2026"},
            {"date_from": 20260921},
            {"date_from": "2026-09-21", "kind": "c"},
            {"date_from": "2026-09-21", "limit": "3"},
            {"date_from": "2026-09-21", "limit": True},
            {"date_from": "2026-09-21", "limit": 11},
            {"date_from": "2026-09-21", "property_ids": [1, 2, 3, 4]},
            {"date_from": "2026-09-21", "property_ids": ["1"]},
        ]
        for kwargs in bad_calls:
            with self.subTest(kwargs=kwargs), self.assertRaises(UserError):
                self._run("echo", self.user_a, **kwargs)

    def test_read_tool_cannot_write(self):
        for via in ("orm", "sql"):
            with self.subTest(via=via), mute_logger(TOOLS_LOGGER, SQL_LOGGER):
                with self.assertRaisesRegex(UserError, "nothing was saved"):
                    self._run(
                        "sneaky_write", self.user_a, partner_id=self.partner.id, via=via
                    )
                self.assertEqual(self._db_ref(), "orig")
                self.partner.invalidate_recordset(["ref"])
                self.assertEqual(self.partner.ref, "orig")
        # The transaction is usable and writable again afterwards.
        self.assertEqual(self._show("transaction_read_only"), "off")
        self.partner.ref = "after"
        self.env.flush_all()
        self.assertEqual(self._db_ref(), "after")

    def test_read_tool_has_a_statement_timeout(self):
        before = self._show("statement_timeout")
        with mute_logger(SQL_LOGGER), self.assertRaisesRegex(
            UserError, "was cancelled"
        ):
            self._run("slow", self.user_a)
        self.assertEqual(self._show("statement_timeout"), before)

    def test_unexpected_errors_are_plain(self):
        with mute_logger(TOOLS_LOGGER), self.assertRaises(UserError) as error:
            self._run("crash", self.user_a)
        self.assertIn("Internal error", str(error.exception))
        self.assertNotIn("secret internals", str(error.exception))

    def test_allowed_users(self):
        self.tools["echo"].allowed_user_ids = self.user_a
        self.assertTrue(self._run("echo", self.user_a, date_from="2026-09-21"))
        with self.assertRaises(AccessError):
            self._run("echo", self.user_b, date_from="2026-09-21")

    # Write tools

    def test_write_tool_needs_explicit_users(self):
        self.tools["set_ref"].allowed_user_ids = False
        with self.assertRaises(AccessError):
            self._run("set_ref", self.user_a, partner_id=self.partner.id, new_ref="x")

    def test_write_tool_previews_then_applies(self):
        timeout_before = self._show("statement_timeout")
        preview = self._run(
            "set_ref", self.user_a, partner_id=self.partner.id, new_ref="new"
        )
        self.assertEqual(self._db_ref(), "orig")
        self.assertEqual(
            preview["result"],
            {"partner_id": self.partner.id, "old": "orig", "new": "new"},
        )
        confirmation = preview["confirmation"]
        self.assertTrue(confirmation["required"])
        applied = self._run(
            "set_ref",
            self.user_a,
            partner_id=self.partner.id,
            new_ref="new",
            confirm=True,
            confirmation_token=confirmation["confirmation_token"],
        )
        self.assertEqual(applied["result"], {"done": True})
        self.assertEqual(applied["summary"], "Reference changed")
        self.assertEqual(self._db_ref(), "new")
        self.assertEqual(self._show("statement_timeout"), timeout_before)

    def _preview_token(self, user=None, new_ref="new"):
        preview = self._run(
            "set_ref", user or self.user_a, partner_id=self.partner.id, new_ref=new_ref
        )
        return preview["confirmation"]["confirmation_token"]

    def _confirm(self, token, user=None, new_ref="new"):
        return self._run(
            "set_ref",
            user or self.user_a,
            partner_id=self.partner.id,
            new_ref=new_ref,
            confirm=True,
            confirmation_token=token,
        )

    def test_confirmation_expires(self):
        with freeze_time("2026-10-15 10:00:00"):
            token = self._preview_token()
        with freeze_time("2026-10-15 10:11:00"), self.assertRaisesRegex(
            UserError, "expired"
        ):
            self._confirm(token)
        self.assertEqual(self._db_ref(), "orig")

    def test_confirmation_is_bound_to_the_user(self):
        self.tools["set_ref"].allowed_user_ids |= self.user_b
        token = self._preview_token(self.user_a)
        with self.assertRaisesRegex(UserError, "does not match"):
            self._confirm(token, user=self.user_b)
        self.assertEqual(self._db_ref(), "orig")

    def test_confirmation_is_bound_to_the_arguments(self):
        token = self._preview_token(new_ref="new")
        with self.assertRaisesRegex(UserError, "does not match"):
            self._confirm(token, new_ref="other")
        self.assertEqual(self._db_ref(), "orig")

    def test_confirmation_is_bound_to_the_data(self):
        token = self._preview_token()
        self.partner.ref = "moved"
        with self.assertRaisesRegex(UserError, "does not match"):
            self._confirm(token)
        self.assertEqual(self._db_ref(), "moved")

    def test_confirmation_needs_a_valid_token(self):
        for token in (None, "", "garbage", "123.abc"):
            with self.subTest(token=token), self.assertRaises(UserError):
                self._confirm(token)
        self.assertEqual(self._db_ref(), "orig")

    def test_writes_can_be_switched_off(self):
        params = self.env["ir.config_parameter"].sudo()
        for global_flag, tool_flag in (("False", True), ("True", False)):
            with self.subTest(global_flag=global_flag, tool_flag=tool_flag):
                params.set_param(PARAM_WRITE_ENABLED, global_flag)
                self.tools["set_ref"].write_enabled = tool_flag
                preview = self._run(
                    "set_ref", self.user_a, partner_id=self.partner.id, new_ref="new"
                )
                self.assertIn("disabled", preview["warnings"][-1])
                with self.assertRaisesRegex(UserError, "disabled"):
                    self._confirm(preview["confirmation"]["confirmation_token"])
                self.assertEqual(self._db_ref(), "orig")

    # Declaration

    def test_tool_definition(self):
        read = self.tools["echo"]._get_tool_definition()
        self.assertTrue(read["annotations"]["readOnlyHint"])
        self.assertFalse(read["annotations"]["destructiveHint"])
        self.assertEqual(read["inputSchema"]["required"], ["date_from"])
        self.assertIn("result", read["outputSchema"]["properties"])
        self.assertEqual(self.tools["echo"].guard_mode, "read")
        write = self.tools["set_ref"]._get_tool_definition()
        self.assertTrue(write["annotations"]["destructiveHint"])
        self.assertIn("confirm", write["inputSchema"]["properties"])
        self.assertIn("confirmation_token", write["inputSchema"]["properties"])
        self.assertEqual(self.tools["set_ref"].guard_mode, "write")
        self.tools["echo"].description = False
        self.assertEqual(self.tools["echo"]._get_tool_definition()["description"], "")
        date_tool = self.env.ref("ai_tool.current_date")
        self.assertEqual(date_tool.guard_mode, "unguarded")
        self.assertNotIn("annotations", date_tool._get_tool_definition())

    def test_unsafe_declarations_are_refused(self):
        def _private(self):
            return {}

        def public(self):
            return {}

        declarations = [
            ({"text": {"type": "string"}}, {}, _private),
            ({"ids": {"type": "array", "items": {"type": "integer"}}}, {}, _private),
            ({"domain": {"type": "object"}}, {}, _private),
            ({}, {"write": True}, _private),
            ({}, {"apply": "_apply"}, _private),
            (
                {"confirm": {"type": "boolean"}},
                {"write": True, "apply": "_a"},
                _private,
            ),
            ({}, {"required_inputs": ["missing"]}, _private),
            ({}, {}, public),
        ]
        for schema, options, func in declarations:
            with self.subTest(schema=schema, options=options), self.assertRaises(
                ValueError
            ):
                guarded_tool(schema, **options)(func)

    # Helpers for the tools

    def test_scope_helpers(self):
        tools = self.env["pms.ai.tools"]
        plan = self.env["pms.availability.plan"].create({"name": "AI plan"})
        pricelist = self.env["product.pricelist"].create(
            {"name": "AI pricelist", "availability_plan_id": plan.id}
        )
        properties = self.env["pms.property"].create(
            [
                {
                    "name": f"AI property {n}",
                    "company_id": self.env.company.id,
                    "default_pricelist_id": pricelist.id,
                }
                for n in (1, 2)
            ]
        )
        self.user_a.write(
            {
                "pms_property_ids": [(6, 0, properties[0].ids)],
                "pms_property_id": properties[0].id,
            }
        )
        tools = tools.with_user(self.user_a)
        self.assertEqual(tools._ai_scope_property_ids(), properties[0].ids)
        self.assertEqual(
            tools._ai_scope_property_ids(properties[0].ids), properties[0].ids
        )
        with self.assertRaises(AccessError):
            tools._ai_scope_property_ids(properties.ids)
        today = date(2026, 10, 15)
        tools._ai_check_period(today, today + timedelta(days=30), 31)
        with self.assertRaises(UserError):
            tools._ai_check_period(today, today + timedelta(days=31), 31)
        with self.assertRaises(UserError):
            tools._ai_check_period(today, today - timedelta(days=1), 31)
        warnings = []
        rows = tools._ai_limit_rows(list(range(60)), warnings)
        self.assertEqual(len(rows), 50)
        self.assertEqual(len(warnings), 1)
        self.assertEqual(len(tools._ai_limit_rows(list(range(60)), [], limit=5)), 5)
