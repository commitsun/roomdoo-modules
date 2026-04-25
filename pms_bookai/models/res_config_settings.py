import json
import logging

import requests

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

_SETUP_TIMEOUT = 30


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    bookai_base_url = fields.Char(
        string="BooKAI Base URL",
        help="Base URL of the BooKAI service " "(e.g. https://bookai.example.com).",
    )
    bookai_bearer_token = fields.Char(
        string="BooKAI Bearer Token",
        help="Authentication token. Auto-generated on "
        "register. Used for all API calls.",
    )
    bookai_odoo_username = fields.Char(
        string="Odoo Username for BooKAI",
        help="Odoo user that BooKAI will use to connect via SDK "
        "(e.g. bookai@instance.com).",
    )
    bookai_odoo_api_key = fields.Char(
        string="Odoo API Key for BooKAI",
        help="API Key generated for the BooKAI user in Odoo "
        "(Preferences → API Keys).",
    )

    @api.model
    def get_values(self):
        res = super().get_values()
        icp = self.env["ir.config_parameter"].sudo()
        res["bookai_base_url"] = icp.get_param("pms_bookai.api_endpoint", "")
        res["bookai_bearer_token"] = icp.get_param("pms_bookai.api_token", "")
        res["bookai_odoo_username"] = icp.get_param("pms_bookai.odoo_username", "")
        res["bookai_odoo_api_key"] = icp.get_param("pms_bookai.odoo_api_key", "")
        return res

    def set_values(self):
        res = super().set_values()
        icp = self.env["ir.config_parameter"].sudo()
        icp.set_param("pms_bookai.api_endpoint", self.bookai_base_url or "")
        icp.set_param("pms_bookai.api_token", self.bookai_bearer_token or "")
        icp.set_param(
            "pms_bookai.odoo_username",
            self.bookai_odoo_username or "",
        )
        icp.set_param(
            "pms_bookai.odoo_api_key",
            self.bookai_odoo_api_key or "",
        )
        return res

    def action_bookai_setup(self):
        """Re-sync with BooKAI (uses existing bearer token)."""
        self.ensure_one()
        icp = self.env["ir.config_parameter"].sudo()
        base_url = self.bookai_base_url or icp.get_param("pms_bookai.api_endpoint", "")
        token = self.bookai_bearer_token or icp.get_param("pms_bookai.api_token", "")
        if not base_url or not token:
            raise UserError(
                _(
                    "Please configure BooKAI Base URL and "
                    "Bearer Token before connecting."
                )
            )
        odoo_username = self.bookai_odoo_username or icp.get_param(
            "pms_bookai.odoo_username", ""
        )
        odoo_api_key = self.bookai_odoo_api_key or icp.get_param(
            "pms_bookai.odoo_api_key", ""
        )
        if not odoo_username or not odoo_api_key:
            raise UserError(
                _(
                    "Please configure Odoo Username and API Key "
                    "for BooKAI before connecting."
                )
            )
        odoo_url = icp.get_param("web.base.url", "")
        odoo_db = self.env.cr.dbname
        payload = {
            "odoo_url": odoo_url,
            "odoo_db": odoo_db,
            "odoo_username": odoo_username,
            "odoo_api_key": odoo_api_key,
        }
        url = base_url.rstrip("/") + "/api/v1/instances/setup"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        try:
            resp = requests.post(
                url,
                data=json.dumps(payload),
                headers=headers,
                timeout=_SETUP_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
        except requests.exceptions.ConnectionError as exc:
            raise UserError(_("Cannot connect to BooKAI at %s") % base_url) from exc
        except requests.exceptions.Timeout as exc:
            raise UserError(_("BooKAI setup timed out. Try again later.")) from exc
        except requests.exceptions.HTTPError as exc:
            raise UserError(
                _("BooKAI returned HTTP %s: %s")
                % (exc.response.status_code, exc.response.text[:500])
            ) from exc
        except Exception as exc:
            raise UserError(_("BooKAI setup failed: %s") % str(exc)) from exc

        # Reconnect MCP servers after setup
        self._reconnect_mcp_servers()

        return self._handle_setup_response(data)

    def _reconnect_mcp_servers(self):
        """Reconnect all active MCP servers after setup."""
        try:
            self.env["bookai.mcp.server"].action_health_check_all()
        except Exception:
            _logger.warning(
                "MCP reconnect after setup failed",
                exc_info=True,
            )

    def _handle_setup_response(self, data):
        status = data.get("status", "error")
        steps = data.get("steps", [])

        if status == "ok":
            parts = []
            for step in steps:
                if step["step"] == "sync_properties":
                    parts.append(_("Properties synced: %s") % step.get("synced", 0))
                elif step["step"] == "load_agents":
                    parts.append(_("Agents loaded: %s") % step.get("agents_loaded", 0))
            message = _("BooKAI setup completed successfully.")
            if parts:
                message += "\n" + "\n".join(parts)
            return self._show_notification(_("Success"), message, "success")

        if status == "partial":
            failed = [s for s in steps if s.get("status") != "ok"]
            details = "\n".join(
                f"- {s['step']}: {s.get('detail', 'failed')}" for s in failed
            )
            return self._show_notification(
                _("Partial Setup"),
                _("Some steps failed:\n%s") % details,
                "warning",
            )

        # status == "error"
        detail = data.get("detail", "Unknown error")
        raise UserError(_("BooKAI setup failed: %s") % detail)

    @staticmethod
    def _show_notification(title, message, notification_type):
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": title,
                "message": message,
                "type": notification_type,
                "sticky": True,
            },
        }
