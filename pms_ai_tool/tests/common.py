from odoo.tests.common import new_test_user

from ..tools import PARAM_ENVIRONMENT, PARAM_WRITE_ENABLED, guarded_tool


@guarded_tool(
    input_schema={
        "date_from": {"type": "string", "format": "date"},
        "kind": {"type": "string", "enum": ["a", "b"]},
        "limit": {"type": "integer", "minimum": 1, "maximum": 10},
        "property_ids": {
            "type": "array",
            "items": {"type": "integer"},
            "maxItems": 3,
        },
    },
    output_schema={"echo": {"type": "object"}},
    required_inputs=["date_from"],
)
def _test_ai_echo(self, **kwargs):
    return {
        "result": {"echo": kwargs},
        "summary": "Echo",
        "period": {"date_from": kwargs["date_from"]},
        "definitions": ["Echoes its arguments."],
        "warnings": ["Test tool."],
    }


@guarded_tool(
    input_schema={
        "partner_id": {"type": "integer"},
        "via": {"type": "string", "enum": ["orm", "sql"]},
    },
    required_inputs=["partner_id", "via"],
)
def _test_ai_sneaky_write(self, partner_id, via):
    if via == "orm":
        # Even sudo cannot write from a read tool.
        self.env["res.partner"].sudo().browse(partner_id).ref = "changed"
    else:
        self.env.cr.execute(
            "UPDATE res_partner SET ref = 'changed' WHERE id = %s", [partner_id]
        )
    return {"result": {}}


@guarded_tool(input_schema={}, timeout_ms=100)
def _test_ai_slow(self):
    self.env.cr.execute("SELECT pg_sleep(2)")
    return {"result": {}}


@guarded_tool(input_schema={})
def _test_ai_crash(self):
    raise KeyError("secret internals")


@guarded_tool(input_schema={}, log_result=False)
def _test_ai_personal(self):
    return {"result": {"name": "Jane Doe"}, "summary": "One employee"}


@guarded_tool(
    input_schema={
        "partner_id": {"type": "integer"},
        "new_ref": {"type": "string", "maxLength": 20},
    },
    required_inputs=["partner_id", "new_ref"],
    write=True,
    apply="_test_ai_set_ref_apply",
)
def _test_ai_set_ref(self, partner_id, new_ref):
    partner = self.env["res.partner"].browse(partner_id)
    return {
        "result": {"partner_id": partner.id, "old": partner.ref or "", "new": new_ref},
        "summary": "Change the reference",
        "plan": partner,
    }


def _test_ai_set_ref_apply(self, plan, partner_id, new_ref):
    plan.sudo().ref = new_ref
    return {"result": {"done": True}, "summary": "Reference changed"}


TEST_TOOLS = {
    "_test_ai_echo": _test_ai_echo,
    "_test_ai_sneaky_write": _test_ai_sneaky_write,
    "_test_ai_slow": _test_ai_slow,
    "_test_ai_crash": _test_ai_crash,
    "_test_ai_personal": _test_ai_personal,
    "_test_ai_set_ref": _test_ai_set_ref,
    "_test_ai_set_ref_apply": _test_ai_set_ref_apply,
}


class PmsAiToolTestMixin:
    """Register throwaway tools on pms.ai.tools and the records around them."""

    @classmethod
    def _setup_ai_tools(cls):
        model_class = type(cls.env["pms.ai.tools"])
        for name, func in TEST_TOOLS.items():
            setattr(model_class, name, func)
            cls.addClassCleanup(delattr, model_class, name)
        params = cls.env["ir.config_parameter"].sudo()
        params.set_param(PARAM_ENVIRONMENT, "test")
        params.set_param(PARAM_WRITE_ENABLED, "True")
        cls.user_a = new_test_user(cls.env, login="ai_user_a", groups="base.group_user")
        cls.user_b = new_test_user(cls.env, login="ai_user_b", groups="base.group_user")
        cls.partner = cls.env["res.partner"].create({"name": "AI", "ref": "orig"})
        model = cls.env["ir.model"]._get("pms.ai.tools")
        tools = cls.env["ai.tool"].create(
            [
                {
                    "name": name.removeprefix("_test_ai_"),
                    "description": f"Test tool {name}",
                    "model_id": model.id,
                    "function_name": name,
                    "kind": "generic",
                }
                for name in TEST_TOOLS
                if not name.endswith("_apply")
            ]
        )
        cls.tools = {tool.name: tool for tool in tools}
        cls.tools["set_ref"].write(
            {"allowed_user_ids": [(6, 0, cls.user_a.ids)], "write_enabled": True}
        )

    def _run(self, tool_name, user, **kwargs):
        return self.tools[tool_name].with_user(user)._execute_tool(**kwargs)
