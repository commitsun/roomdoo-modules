import hashlib
import hmac
import json
import logging
import time
import uuid

import requests

from odoo import api, fields, models, release
from odoo.exceptions import UserError

from odoo.addons.queue_job.exception import RetryableJobError

_logger = logging.getLogger(__name__)

PARAM_URL = "roomdoo_host_link.host_url"
PARAM_SECRET = "roomdoo_host_link.secret"
PARAM_INSTANCE = "roomdoo_host_link.instance"
PARAM_LAST_HEARTBEAT = "roomdoo_host_link.last_heartbeat"
PARAM_VERSIONS = "roomdoo_host_link.versions_digest"
TECHNICAL_LOGIN = "bookai-soporte"
TECHNICAL_GROUPS = (
    "base.group_user",
    "pms.group_pms_manager",
    "account.group_account_readonly",
    "roomdoo_host_link.group_host_link",
)
WATCHED_MODULES = (
    "pms",
    "pms_api_rest",
    "pms_fastapi",
    "roomdoo_fastapi",
    "roomdoo_host_link",
    "connector_pms_wubook",
    "pms_bookai",
)
HEARTBEAT_EVERY = 55 * 60  # seconds; the cron ticks every 15 minutes
TIMEOUT = 20


class RoomdooHostLink(models.AbstractModel):
    """Tells host what changes in this instance, as it changes.

    Every event is queued (a slow or absent host never blocks anyone working
    in the PMS), names the instance as host knows it, carries a unique id and
    is signed with the instance's own secret. Without a configured host URL,
    secret and instance code, nothing happens.
    """

    _name = "roomdoo.host.link"
    _description = "Roomdoo host link"

    @api.model
    def _config(self):
        icp = self.env["ir.config_parameter"].sudo()
        url = (icp.get_param(PARAM_URL) or "").rstrip("/")
        secret = icp.get_param(PARAM_SECRET) or ""
        instance = icp.get_param(PARAM_INSTANCE) or ""
        if url and secret and instance:
            return url, secret, instance
        return None, None, None

    @api.model
    def _notify(self, kind, data):
        """Queue an event for host. Returns its id, or False when not linked."""
        url, _secret, instance = self._config()
        if not url or self.env.context.get("roomdoo_host_link_mute"):
            return False
        icp = self.env["ir.config_parameter"].sudo()
        event = {
            "id": str(uuid.uuid4()),
            "kind": kind,
            "instance": instance,
            "at": fields.Datetime.to_string(fields.Datetime.now()),
            "db": self.env.cr.dbname,
            "base_url": icp.get_param("web.base.url"),
            "data": data,
        }
        self.sudo().with_delay(
            description=f"Roomdoo host: {kind}", max_retries=12
        )._send(event)
        return event["id"]

    @api.model
    def _sign(self, secret, timestamp, body):
        message = timestamp.encode() + b"." + body
        return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()

    @api.model
    def _send(self, event):
        """Deliver one event. Retried when host is down, not when it refuses it."""
        url, secret, _instance = self._config()
        if not url:
            return "Host link not configured: event dropped."
        body = json.dumps(event, sort_keys=True, default=str).encode()
        timestamp = str(int(time.time()))
        headers = {
            "Content-Type": "application/json",
            "X-Roomdoo-Timestamp": timestamp,
            "X-Roomdoo-Signature": f"sha256={self._sign(secret, timestamp, body)}",
        }
        try:
            response = requests.post(
                f"{url}/roomdoo/registry/events",
                data=body,
                headers=headers,
                timeout=TIMEOUT,
            )
        except requests.RequestException as exc:
            raise RetryableJobError(f"Host unreachable: {exc}", seconds=300) from exc
        # 404: host does not know this instance yet, or is being deployed.
        if response.status_code >= 500 or response.status_code in (404, 429):
            raise RetryableJobError(
                f"Host answered {response.status_code}", seconds=300
            )
        if response.status_code >= 400:
            # Sending it again would be refused again: fail the job, visibly.
            raise UserError(
                f"Host refused the event ({response.status_code}): "
                f"{response.text[:300]}"
            )
        return f"Delivered ({response.status_code})."

    # --- payloads -------------------------------------------------------------

    @api.model
    def _property_data(self, properties):
        return [
            {
                "id": prop.id,
                "name": prop.name,
                "code": prop.pms_property_code or False,
                "email": prop.email or False,
                "ref": prop.ref or False,
            }
            for prop in properties
        ]

    @api.model
    def _user_data(self, users):
        return [
            {
                "id": user.id,
                "name": user.name,
                "login": user.login,
                "email": user.email or False,
                "active": user.active,
            }
            for user in users
            if not user.share and user.login != TECHNICAL_LOGIN
        ]

    @api.model
    def _flag_data(self):
        flags = self.env["feature.flag"].sudo().with_context(active_test=False)
        return [
            {
                "name": flag.name,
                "active": flag.active,
                "whole_instance": flag.is_active_instance,
                "users": len(flag.user_ids),
            }
            for flag in flags.search([])
        ]

    @api.model
    def _versions(self):
        modules = (
            self.env["ir.module.module"]
            .sudo()
            .search([("name", "in", WATCHED_MODULES), ("state", "=", "installed")])
        )
        versions = {module.name: module.latest_version for module in modules}
        versions["server"] = release.version
        return versions

    @api.model
    def _usage(self):
        """How the instance is used, without counting the technical user."""
        technical = (
            self.env["res.users"]
            .sudo()
            .with_context(active_test=False)
            .search([("login", "=", TECHNICAL_LOGIN)], limit=1)
        )
        logins = self.env["res.users.log"].sudo()
        domain = [("create_uid", "!=", technical.id)] if technical else []

        def latest(model, field, model_domain=None):
            record = (
                self.env[model]
                .sudo()
                .search(model_domain or [], order=f"{field} desc", limit=1)
            )
            return fields.Datetime.to_string(record[field]) if record else False

        return {
            "hotels": self.env["pms.property"].sudo().search_count([]),
            "last_reservation_at": latest("pms.reservation", "create_date"),
            "last_price_change_at": latest("product.pricelist.item", "write_date"),
            "last_login_at": fields.Datetime.to_string(
                logins.search(domain, order="id desc", limit=1).create_date
            )
            or False,
            "versions": self._versions(),
        }

    # --- cron -----------------------------------------------------------------

    @api.model
    def _cron_tick(self):
        """Every 15 minutes: versions when they changed, usage every hour."""
        url, _secret, _instance = self._config()
        if not url:
            return
        icp = self.env["ir.config_parameter"].sudo()
        versions = self._versions()
        digest = hashlib.sha256(
            json.dumps(versions, sort_keys=True).encode()
        ).hexdigest()
        if digest != icp.get_param(PARAM_VERSIONS):
            self._notify("versions", versions)
            icp.set_param(PARAM_VERSIONS, digest)
        last = float(icp.get_param(PARAM_LAST_HEARTBEAT) or 0)
        if time.time() - last >= HEARTBEAT_EVERY:
            self._notify("heartbeat", self._usage())
            icp.set_param(PARAM_LAST_HEARTBEAT, str(time.time()))

    # --- technical user -------------------------------------------------------

    @api.model
    def _ensure_technical_user(self):
        """The user the operations tools log in as: adopted if it exists."""
        users = (
            self.env["res.users"]
            .sudo()
            .with_context(active_test=False, no_reset_password=True)
        )
        groups = self.env["res.groups"]
        for xmlid in TECHNICAL_GROUPS:
            groups |= self.env.ref(xmlid, raise_if_not_found=False) or groups
        user = users.search([("login", "=", TECHNICAL_LOGIN)], limit=1)
        if user:
            user.write({"groups_id": [(4, group.id) for group in groups]})
            return user
        properties = self.env["pms.property"].sudo().search([])
        companies = self.env["res.company"].sudo().search([])
        return users.create(
            {
                "name": "BookAI soporte",
                "login": TECHNICAL_LOGIN,
                "groups_id": [(6, 0, groups.ids)],
                "company_ids": [(6, 0, companies.ids)],
                "company_id": (properties[:1].company_id or companies[:1]).id,
                "pms_property_ids": [(6, 0, properties.ids)],
                "pms_property_id": properties[:1].id,
            }
        )
