from datetime import datetime, timezone

import pytz

from odoo import _, api, fields, models
from odoo.exceptions import AccessDenied
from odoo.fields import Command

# Whatever host asks for, nobody of the team stays in longer than this.
MAX_HOURS = 24


def _utc(epoch):
    return datetime.fromtimestamp(epoch, tz=timezone.utc).replace(tzinfo=None)


class ResUsers(models.Model):
    _inherit = "res.users"

    host_sso_user = fields.Boolean(
        "Created by host",
        readonly=True,
        copy=False,
        help="A person of the Roomdoo team, let in from host. The user is "
        "archived when the access ends.",
    )
    host_sso_until = fields.Datetime("Host access until", readonly=True, copy=False)

    def _check_credentials(self, password, env):
        # The Roomdoo team only comes in through host: never with a password,
        # a key or anything else other modules may accept.
        if self.env.user.sudo().host_sso_user:
            raise AccessDenied()
        return super()._check_credentials(password, env)

    @api.model
    def _host_sso_provision(self, claims):
        """The user of the person in the token, made or reactivated."""
        login = (claims.get("sub") or "").strip().lower()
        if not login:
            raise AccessDenied(_("The entry link does not say who enters."))
        users = self.sudo().with_context(active_test=False, no_reset_password=True)
        user = users.search([("login", "=", login)], limit=1)
        if user and not user.host_sso_user:
            raise AccessDenied(
                _("This instance already has a user %(login)s of its own.", login=login)
            )
        profile = (
            self.env["roomdoo.host.sso.profile"]
            .sudo()
            .search([("code", "=", claims.get("profile"))], limit=1)
        )
        if not profile:
            raise AccessDenied(_("The entry link asks for an unknown profile."))
        until = _utc(min(claims["until"], claims["iat"] + MAX_HOURS * 3600))
        if user.active and user.host_sso_until and user.host_sso_until > until:
            until = user.host_sso_until
        companies = self.env["res.company"].sudo().search([])
        properties = self.env["pms.property"].sudo().search([])
        values = {
            "name": claims.get("name") or login,
            "email": claims.get("email") or login,
            "active": True,
            "host_sso_user": True,
            "host_sso_until": until,
            "groups_id": [Command.set(profile.group_ids.ids)],
            "company_ids": [Command.set(companies.ids)],
            "company_id": (properties[:1].company_id or companies[:1]).id,
            "pms_property_ids": [Command.set(properties.ids)],
            "pms_property_id": properties[:1].id,
        }
        if claims.get("tz") in pytz.all_timezones_set:
            values["tz"] = claims["tz"]
        if claims.get("lang") and self.env["res.lang"]._lang_get(claims["lang"]):
            values["lang"] = claims["lang"]
        if user:
            user.write(values)
        else:
            user = users.create(dict(values, login=login))
        user.partner_id.signup_cancel()
        return user

    @api.model
    def _cron_archive_host_access(self):
        ended = self.sudo().search(
            [
                ("host_sso_user", "=", True),
                ("host_sso_until", "<", fields.Datetime.now()),
            ]
        )
        ended.write({"active": False})
        return ended
