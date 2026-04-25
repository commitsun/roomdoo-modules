import json
import logging

import requests

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

_REGISTER_TIMEOUT = 30


class BookaiRegisterWizard(models.TransientModel):
    _name = "bookai.register.wizard"
    _description = "BooKAI Instance Registration"

    bookai_base_url = fields.Char(
        string="BooKAI Base URL",
        required=True,
    )
    provisioning_key = fields.Char(
        string="Provisioning Key",
        required=True,
    )
    odoo_username = fields.Char(
        string="Odoo Username",
        required=True,
    )
    odoo_api_key = fields.Char(
        string="Odoo API Key",
        required=True,
    )

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        icp = self.env["ir.config_parameter"].sudo()
        res["bookai_base_url"] = icp.get_param("pms_bookai.api_endpoint", "")
        res["odoo_username"] = icp.get_param("pms_bookai.odoo_username", "")
        res["odoo_api_key"] = icp.get_param("pms_bookai.odoo_api_key", "")
        return res

    def action_register(self):
        self.ensure_one()
        icp = self.env["ir.config_parameter"].sudo()
        odoo_url = icp.get_param("web.base.url", "")
        odoo_db = self.env.cr.dbname

        payload = {
            "odoo_url": odoo_url,
            "odoo_db": odoo_db,
            "odoo_username": self.odoo_username,
            "odoo_api_key": self.odoo_api_key,
        }
        url = self.bookai_base_url.rstrip("/") + "/api/v1/instances/register"
        headers = {
            "X-Provisioning-Key": self.provisioning_key,
            "Content-Type": "application/json",
        }
        try:
            resp = requests.post(
                url,
                data=json.dumps(payload),
                headers=headers,
                timeout=_REGISTER_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
        except requests.exceptions.ConnectionError as exc:
            raise UserError(
                _("Cannot connect to BooKAI at %s") % self.bookai_base_url
            ) from exc
        except requests.exceptions.Timeout as exc:
            raise UserError(_("Registration timed out.")) from exc
        except requests.exceptions.HTTPError as exc:
            raise UserError(
                _("BooKAI returned HTTP %s: %s")
                % (
                    exc.response.status_code,
                    exc.response.text[:500],
                )
            ) from exc

        # Persist config
        bearer_token = data.get("bearer_token", "")
        if bearer_token:
            icp.set_param("pms_bookai.api_token", bearer_token)
        icp.set_param(
            "pms_bookai.api_endpoint",
            self.bookai_base_url,
        )
        icp.set_param(
            "pms_bookai.odoo_username",
            self.odoo_username,
        )
        icp.set_param(
            "pms_bookai.odoo_api_key",
            self.odoo_api_key,
        )

        # Build result message
        status = data.get("status", "error")
        steps = data.get("steps", [])
        parts = []
        for step in steps:
            if step["step"] == "sync_properties":
                parts.append(_("Properties: %s synced") % step.get("synced", 0))
            elif step["step"] == "load_agents":
                parts.append(_("Agents: %s loaded") % step.get("agents_loaded", 0))
        msg = _("Registration successful!")
        if parts:
            msg += "\n" + "\n".join(parts)

        if status == "partial":
            failed = [s for s in steps if s.get("status") != "ok"]
            if failed:
                details = "\n".join(
                    f"- {s['step']}: " f"{s.get('detail', 'failed')}" for s in failed
                )
                msg += _("\n\nSome steps failed:\n%s") % details

        if status == "error":
            raise UserError(
                _("Registration failed: %s") % data.get("detail", "Unknown error")
            )

        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("BooKAI"),
                "message": msg,
                "type": ("success" if status == "ok" else "warning"),
                "sticky": True,
            },
        }
