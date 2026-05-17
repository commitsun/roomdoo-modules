import json
import logging

import requests

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

_SYNC_TIMEOUT = 15


class BookaiTool(models.Model):
    _name = "bookai.tool"
    _inherit = ["bookai.webhook.mixin"]
    _description = "BooKAI Tool"
    _order = "tool_type, name, id"

    _bookai_webhook_path = "/webhooks/tool-updated"
    _bookai_webhook_event = "tool_updated"

    name = fields.Char(required=True)
    description = fields.Text(required=True)

    agent_binding_ids = fields.One2many(
        "bookai.agent.tool.binding",
        "tool_id",
        string="Agent Bindings",
    )
    agent_count = fields.Integer(
        compute="_compute_agent_count",
        string="Agents",
    )

    def _compute_agent_count(self):
        for rec in self:
            rec.agent_count = len(rec.agent_binding_ids)

    tool_type = fields.Selection(
        [
            ("sdk", "SDK"),
            ("mcp", "MCP"),
            ("webhook", "Webhook"),
            ("http", "HTTP"),
            ("odoo_action", "Odoo Action"),
            ("function", "Function"),
        ],
        required=True,
    )
    input_schema = fields.Text(
        help="JSON Schema for tool parameters.",
    )
    requires_confirm = fields.Boolean(
        default=False,
        help="Legacy field. Use action_sensitivity instead.",
    )
    action_sensitivity = fields.Selection(
        [
            ("none", "None"),
            ("sensitive", "Sensitive"),
            ("irreversible", "Irreversible"),
        ],
        default="none",
        help=(
            "None: no confirmation needed.\n"
            "Sensitive: confirm when policy requires it.\n"
            "Irreversible: always confirm (delete, cancel)."
        ),
    )
    active = fields.Boolean(default=True)

    # SDK
    sdk_method = fields.Char(
        help='SDK method path, e.g. "folios.get_folio".',
    )

    # MCP
    mcp_server_id = fields.Many2one(
        "bookai.mcp.server",
        string="MCP Server",
        ondelete="set null",
    )
    mcp_tool_name = fields.Char(
        help="Exact tool name on the MCP server.",
    )

    # Webhook / HTTP
    endpoint_url = fields.Char()
    endpoint_headers = fields.Text(
        help="JSON with additional HTTP headers.",
    )

    # Odoo Action
    odoo_model = fields.Char(
        help='Odoo model, e.g. "pms.reservation".',
    )
    odoo_method = fields.Char(
        help='Odoo method, e.g. "get_active_for_conversation".',
    )

    # Function
    function_ref = fields.Char(
        help="Internal BooKAI function reference.",
    )

    _sql_constraints = [
        (
            "name_type_unique",
            "unique(name, tool_type)",
            "Tool name must be unique per type.",
        ),
    ]

    # ------------------------------------------------------------------
    # Webhook payload
    # ------------------------------------------------------------------
    def _bookai_webhook_payload(self):
        return [
            {"tool_id": rec.id, "name": rec.name, "tool_type": rec.tool_type}
            for rec in self
        ]

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._notify_bookai_webhook("upsert")
        return records

    def write(self, vals):
        result = super().write(vals)
        self._notify_bookai_webhook("upsert")
        return result

    def unlink(self):
        webhook_data = self._bookai_webhook_payload()
        result = super().unlink()
        self._notify_bookai_webhook_delete(webhook_data)
        return result

    @api.model
    def action_sync_sdk_tools(self):
        """Sync SDK tools from BooKAI endpoint."""
        icp = self.env["ir.config_parameter"].sudo()
        base_url = icp.get_param("pms_bookai.api_endpoint", "")
        token = icp.get_param("pms_bookai.api_token", "")
        if not base_url or not token:
            _logger.warning("SDK tools sync skipped: no BooKAI config.")
            return
        url = base_url.rstrip("/") + "/api/v1/sdk/tools"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        try:
            resp = requests.get(url, headers=headers, timeout=_SYNC_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            _logger.warning(
                "SDK tools sync failed: %s",
                url,
                exc_info=True,
            )
            return

        remote_tools = data.get("tools", [])
        remote_names = set()
        created = 0
        updated = 0

        for tool_data in remote_tools:
            name = tool_data.get("name", "")
            if not name:
                continue
            remote_names.add(name)
            existing = self.with_context(active_test=False).search(
                [("name", "=", name), ("tool_type", "=", "sdk")],
                limit=1,
            )
            vals = {
                "description": tool_data.get("description", ""),
                "input_schema": json.dumps(tool_data.get("input_schema", {})),
                "sdk_method": tool_data.get("sdk_method", ""),
            }
            if existing:
                if not existing.active:
                    vals["active"] = True
                existing.write(vals)
                updated += 1
            else:
                vals.update(
                    {
                        "name": name,
                        "tool_type": "sdk",
                        "active": True,
                        "requires_confirm": tool_data.get("requires_confirm", False),
                    }
                )
                self.create(vals)
                created += 1

        # Archive SDK tools that no longer exist
        archived = 0
        stale = self.search(
            [
                ("tool_type", "=", "sdk"),
                ("name", "not in", list(remote_names)),
                ("active", "=", True),
            ]
        )
        if stale:
            stale.write({"active": False})
            archived = len(stale)

        _logger.info(
            "SDK tools sync: %d created, %d updated, " "%d archived.",
            created,
            updated,
            archived,
        )
