from odoo.tests.common import new_test_user

from odoo.addons.pms_ai_tool.tools import PARAM_ENVIRONMENT

ENVELOPE_KEYS = (
    "environment",
    "generated_at",
    "tool",
    "summary",
    "period",
    "definitions",
    "warnings",
    "result",
)


class AdminToolsMixin:
    """Chart of accounts, two properties and users shared by the tests."""

    @classmethod
    def _setup_admin(cls):
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        # Invoices and cash journals need a chart of accounts, which only exists
        # in some databases; CI ones may have none.
        company = cls.env.ref("base.main_company")
        if not company.chart_template_id:
            coa = cls.env.ref("l10n_generic_coa.configurable_chart_template", False)
            if not coa:
                coa = cls.env["account.chart.template"].search(
                    [("visible", "=", True)], limit=1
                )
            if not coa:
                cls.skipTest(cls, "No chart of accounts available.")
            coa.try_loading(company=company, install_demo=False)
        cls.env["ir.config_parameter"].sudo().set_param(PARAM_ENVIRONMENT, "test")
        plan = cls.env["pms.availability.plan"].create({"name": "AI admin plan"})
        pricelist = cls.env["product.pricelist"].create(
            {
                "name": "AI admin pricelist",
                "availability_plan_id": plan.id,
                "is_pms_available": True,
            }
        )
        # As the pms tests do: every pricelist usable by the PMS (agencies get
        # the default one).
        for other in cls.env["product.pricelist"].search([]):
            if not other.availability_plan_id:
                other.write({"availability_plan_id": plan.id, "is_pms_available": True})
        cls.prop, cls.other_prop = cls.env["pms.property"].create(
            [
                {
                    "name": name,
                    "company_id": cls.env.company.id,
                    "default_pricelist_id": pricelist.id,
                }
                for name in ("AI admin hotel", "AI admin other hotel")
            ]
        )
        room_class = cls.env["pms.room.type.class"].create(
            {"name": "AI admin class", "default_code": "AIA"}
        )
        cls.room_type = cls.env["pms.room.type"].create(
            {
                "pms_property_ids": [(6, 0, (cls.prop | cls.other_prop).ids)],
                "name": "AI admin double",
                "default_code": "AIAD",
                "class_id": room_class.id,
            }
        )
        cls.env["pms.room"].create(
            [
                {
                    "pms_property_id": prop.id,
                    "name": f"Room {n}",
                    "short_name": f"A{n}",
                    "room_type_id": cls.room_type.id,
                    "capacity": 2,
                }
                for prop in (cls.prop, cls.other_prop)
                for n in range(6)
            ]
        )
        cls.direct = cls.env["pms.sale.channel"].create(
            {"name": "AI admin door", "channel_type": "direct"}
        )
        cls.indirect = cls.env["pms.sale.channel"].create(
            {"name": "AI admin agencies", "channel_type": "indirect"}
        )
        accounting = "base.group_user,pms.group_pms_user,account.group_account_user"
        cls.reader = new_test_user(
            cls.env,
            login="ai_admin_reader",
            groups=accounting,
            pms_property_ids=[(6, 0, cls.prop.ids)],
            pms_property_id=cls.prop.id,
        )
        cls.reader_both = new_test_user(
            cls.env,
            login="ai_admin_reader_both",
            groups=accounting,
            pms_property_ids=[(6, 0, (cls.prop | cls.other_prop).ids)],
            pms_property_id=cls.prop.id,
        )
        cls.pms_only = new_test_user(
            cls.env,
            login="ai_admin_pms_only",
            groups="base.group_user,pms.group_pms_user",
            pms_property_ids=[(6, 0, cls.prop.ids)],
            pms_property_id=cls.prop.id,
        )

    def _run(self, tool, user=None, **kwargs):
        return (
            self.env.ref(f"pms_ai_tool_admin.ai_tool_{tool}")
            .with_user(user or self.reader)
            ._execute_tool(**kwargs)
        )

    def _assert_read_tool(self, tool, response):
        """The common envelope, and the tool is declared as read only."""
        for key in ENVELOPE_KEYS:
            self.assertIn(key, response)
        self.assertEqual(response["environment"], "test")
        self.assertEqual(response["tool"], tool)
        self.assertTrue(response["summary"])
        self.assertTrue(response["definitions"])
        record = self.env.ref(f"pms_ai_tool_admin.ai_tool_{tool}")
        self.assertEqual(record.guard_mode, "read")
        annotations = record._get_tool_definition()["annotations"]
        self.assertTrue(annotations["readOnlyHint"])
