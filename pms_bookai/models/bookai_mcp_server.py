import json
import logging

import requests

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

_REQUEST_TIMEOUT = 30


class BookaiMcpServer(models.Model):
    _name = "bookai.mcp.server"
    _inherit = ["bookai.webhook.mixin"]
    _description = "BooKAI MCP Server"
    _order = "name, id"

    _bookai_webhook_path = "/webhooks/mcp-server-updated"
    _bookai_webhook_event = "mcp_server_updated"
    _bookai_webhook_skip_fields = {
        "connection_status",
        "status_message",
        "last_discovery_at",
    }

    name = fields.Char(required=True)
    transport_type = fields.Selection(
        [
            ("stdio", "Local (stdio)"),
            ("http", "Remote (HTTP)"),
        ],
        required=True,
        default="stdio",
    )
    active = fields.Boolean(default=True)
    notes = fields.Text()
    last_discovery_at = fields.Datetime(readonly=True)

    # stdio transport
    command = fields.Char(
        help='Executable: "npx", "uvx", "python", etc.',
    )
    args = fields.Char(
        help="Arguments, e.g. " '"@anthropic/mcp-server-brave-search".',
    )
    env_vars = fields.Text(
        help='JSON: {"BRAVE_API_KEY": "xxx", "...": "..."}',
    )

    # http transport
    url = fields.Char(string="Server URL")
    api_key = fields.Char(string="API Key")
    auth_type = fields.Selection(
        [
            ("bearer", "Bearer Token"),
            ("none", "None"),
        ],
        default="bearer",
    )

    # Connection status (readonly, from BooKAI)
    connection_status = fields.Selection(
        [
            ("disconnected", "Disconnected"),
            ("connected", "Connected"),
            ("error", "Error"),
        ],
        default="disconnected",
        readonly=True,
    )
    status_message = fields.Char(readonly=True)

    # Tools
    tool_ids = fields.One2many(
        "bookai.tool",
        "mcp_server_id",
        string="Tools",
    )

    # -----------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------

    def _reload_with_notification(self, title, message, ntype="success"):
        """Return action that reloads the form and shows a notification."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "current",
            "context": {
                "notification": {
                    "title": title,
                    "message": message,
                    "type": ntype,
                },
            },
        }

    def _bookai_request(self, method, path, payload=None):
        if method not in ("get", "post", "patch", "put", "delete"):
            raise ValueError("Invalid HTTP method: %s" % method)
        base_url, token = self._get_bookai_config()
        if not base_url or not token:
            raise UserError(
                _("Configure BooKAI Base URL and Bearer Token " "in Settings first.")
            )
        url = base_url.rstrip("/") + path
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        try:
            resp = getattr(requests, method)(
                url,
                data=json.dumps(payload or {}),
                headers=headers,
                timeout=_REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.ConnectionError as exc:
            raise UserError(_("Cannot connect to BooKAI.")) from exc
        except requests.exceptions.Timeout as exc:
            raise UserError(_("BooKAI request timed out.")) from exc
        except requests.exceptions.HTTPError as exc:
            raise UserError(
                _("BooKAI returned HTTP %s: %s")
                % (exc.response.status_code, exc.response.text[:500])
            ) from exc

    def _build_server_payload(self):
        self.ensure_one()
        payload = {
            "server_id": self.id,
            "name": self.name,
            "transport_type": self.transport_type,
        }
        if self.transport_type == "stdio":
            payload["command"] = self.command or ""
            payload["args"] = self.args or ""
            env = {}
            if self.env_vars:
                try:
                    env = json.loads(self.env_vars)
                except (json.JSONDecodeError, TypeError):
                    pass
            payload["env_vars"] = env
        else:
            payload["url"] = self.url or ""
            payload["api_key"] = self.api_key or ""
            payload["auth_type"] = self.auth_type or "bearer"
        return payload

    # -----------------------------------------------------------------
    # Actions
    # -----------------------------------------------------------------

    def action_discover_tools(self):
        """Ask BooKAI to connect to the MCP server and discover tools."""
        self.ensure_one()
        payload = self._build_server_payload()
        data = self._bookai_request(
            "post",
            f"/api/v1/mcp/servers/{self.id}/discover",
            payload,
        )

        remote_tools = data.get("tools", [])
        remote_names = set()
        created = 0
        updated = 0
        Tool = self.env["bookai.tool"]

        for tool_data in remote_tools:
            tool_name = tool_data.get("name", "")
            if not tool_name:
                continue
            remote_names.add(tool_name)
            existing = Tool.search(
                [
                    ("mcp_server_id", "=", self.id),
                    ("mcp_tool_name", "=", tool_name),
                ],
                limit=1,
            )
            vals = {
                "description": tool_data.get("description", ""),
                "input_schema": json.dumps(tool_data.get("inputSchema", {})),
            }
            if existing:
                existing.write(vals)
                updated += 1
            else:
                vals.update(
                    {
                        "name": tool_name,
                        "tool_type": "mcp",
                        "mcp_server_id": self.id,
                        "mcp_tool_name": tool_name,
                        "active": True,
                    }
                )
                Tool.create(vals)
                created += 1

        # Archive stale tools
        archived = 0
        stale = Tool.search(
            [
                ("mcp_server_id", "=", self.id),
                ("mcp_tool_name", "not in", list(remote_names)),
                ("active", "=", True),
            ]
        )
        if stale:
            stale.write({"active": False})
            archived = len(stale)

        # Update status
        self.write(
            {
                "last_discovery_at": fields.Datetime.now(),
                "connection_status": "connected",
                "status_message": "",
            }
        )

        return self._reload_with_notification(
            _("MCP Discovery Complete"),
            _("%d tools created, %d updated, %d archived.")
            % (created, updated, archived),
        )

    def action_connect(self):
        """Ask BooKAI to start/connect the MCP server."""
        self.ensure_one()
        payload = self._build_server_payload()
        data = self._bookai_request(
            "post",
            f"/api/v1/mcp/servers/{self.id}/connect",
            payload,
        )
        status = data.get("status", "error")
        self.write(
            {
                "connection_status": ("connected" if status == "ok" else "error"),
                "status_message": data.get("message", ""),
            }
        )
        ntype = "success" if status == "ok" else "danger"
        return self._reload_with_notification(
            self.name,
            data.get("message", status),
            ntype,
        )

    def action_disconnect(self):
        """Ask BooKAI to stop the MCP server."""
        self.ensure_one()
        self._bookai_request(
            "post",
            f"/api/v1/mcp/servers/{self.id}/disconnect",
        )
        self.write(
            {
                "connection_status": "disconnected",
                "status_message": "",
            }
        )
        return self._reload_with_notification(self.name, _("Disconnected."), "info")

    def action_check_status(self):
        """Check health of this MCP server via BooKAI."""
        self.ensure_one()
        try:
            data = self._bookai_request(
                "get",
                f"/api/v1/mcp/servers/{self.id}/status",
            )
        except UserError:
            self.write(
                {
                    "connection_status": "error",
                    "status_message": "BooKAI unreachable",
                }
            )
            return self._reload_with_notification(
                self.name, _("BooKAI unreachable"), "danger"
            )
        connected = data.get("connected", False)
        self.write(
            {
                "connection_status": ("connected" if connected else "disconnected"),
                "status_message": data.get("message", ""),
            }
        )
        if not connected:
            return self._reload_with_notification(
                self.name, _("Server disconnected"), "warning"
            )
        return self._reload_with_notification(
            self.name, _("Server connected"), "success"
        )

    @api.model
    def action_health_check_all(self):
        """Cron: check all active MCP servers and reconnect."""
        icp = self.env["ir.config_parameter"].sudo()
        base_url = icp.get_param("pms_bookai.api_endpoint", "")
        token = icp.get_param("pms_bookai.api_token", "")
        if not base_url or not token:
            return
        url = base_url.rstrip("/") + "/api/v1/mcp/servers/status"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        try:
            resp = requests.get(url, headers=headers, timeout=_REQUEST_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            _logger.warning(
                "MCP health check failed",
                exc_info=True,
            )
            return

        status_map = {s["server_id"]: s for s in data.get("servers", [])}
        servers = self.search([("active", "=", True)])
        for server in servers:
            info = status_map.get(server.id)
            if info and info.get("connected"):
                server.write(
                    {
                        "connection_status": "connected",
                        "status_message": "",
                    }
                )
            else:
                # Reconnect
                server.write(
                    {
                        "connection_status": "disconnected",
                    }
                )
                try:
                    server.action_connect()
                except Exception:
                    _logger.warning(
                        "MCP reconnect failed for %s",
                        server.name,
                        exc_info=True,
                    )

    # -----------------------------------------------------------------
    # Webhook payload
    # -----------------------------------------------------------------
    def _bookai_webhook_payload(self):
        return [{"server_id": rec.id, "name": rec.name} for rec in self]

    # -----------------------------------------------------------------
    # CRUD + webhooks
    # -----------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._notify_bookai_webhook("upsert")
        return records

    def write(self, vals):
        result = super().write(vals)
        skip = self._bookai_webhook_skip_fields
        if not skip.issuperset(vals.keys()):
            self._notify_bookai_webhook("upsert")
        return result

    def unlink(self):
        webhook_data = self._bookai_webhook_payload()
        result = super().unlink()
        self._notify_bookai_webhook_delete(webhook_data)
        return result
