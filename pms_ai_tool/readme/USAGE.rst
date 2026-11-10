Connect Claude Code with the server URL and the personal key, for example::

    claude mcp add --transport http odoo https://<host>/mcp/<server key> \
        --header "Authorization: Bearer <personal key>"

To declare a tool::

    from odoo.addons.pms_ai_tool.tools import guarded_tool

    class PmsAiTools(models.AbstractModel):
        _inherit = "pms.ai.tools"

        @guarded_tool(
            input_schema={
                "date_from": {"type": "string", "format": "date"},
                "date_to": {"type": "string", "format": "date"},
            },
            output_schema={"nights": {"type": "integer"}},
            required_inputs=["date_from", "date_to"],
        )
        def _ai_nights(self, date_from, date_to):
            period = self._ai_check_period(date_from, date_to, 93)
            property_ids = self._ai_scope_property_ids()
            ...
            return {"result": {"nights": nights}, "period": period}

and register it with an ``ai.tool`` record of kind *generic* pointing to
``pms.ai.tools`` and the method name.
