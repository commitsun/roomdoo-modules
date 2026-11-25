import json
from datetime import datetime, timezone

import pytz

from odoo import _, api, fields, models
from odoo.exceptions import AccessDenied
from odoo.fields import Command

# Whatever host asks for, nobody of the team stays in longer than this.
MAX_HOURS = 24
# Logins whose portal user of this instance host may take over for an access
# and give back when it ends (comma separated e-mail domains).
PARAM_ADOPTABLE = "roomdoo_host_sso.adoptable_domains"
DEFAULT_ADOPTABLE = "roomdoo.com"
# What an adopted portal user had, to give it back as it was.
PORTAL_FIELDS = ("name", "email", "lang", "tz", "active")
PORTAL_RELATIONS = (
    "groups_id",
    "company_ids",
    "company_id",
    "pms_property_ids",
    "pms_property_id",
)


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
    host_sso_adopted = fields.Boolean(
        "Portal user taken over by host",
        readonly=True,
        copy=False,
        help="The person's own portal user of this instance. During an access "
        "from host it is an internal user that only host lets in; when the "
        "access ends it is given back as it was, password included.",
    )
    host_sso_portal_state = fields.Text(
        readonly=True,
        copy=False,
        groups="base.group_system",
        help="What the portal user had before the access, to give it back.",
    )

    def _host_sso_in_access(self):
        """True while host is the only way in for this user."""
        self.ensure_one()
        return self.host_sso_user and not (self.host_sso_adopted and self.share)

    def _check_credentials(self, password, env):
        # The Roomdoo team only comes in through host: never with a password,
        # a key or anything else other modules may accept. An adopted portal
        # user is back to its own credentials once the access has ended.
        if self.env.user.sudo()._host_sso_in_access():
            raise AccessDenied()
        return super()._check_credentials(password, env)

    def _host_sso_adoptable(self):
        """A portal user of a team login: host may take it over for an access."""
        self.ensure_one()
        if not self.share or self.has_group("base.group_user"):
            return False
        domains = (
            self.env["ir.config_parameter"]
            .sudo()
            .get_param(PARAM_ADOPTABLE, DEFAULT_ADOPTABLE)
        )
        allowed = {d.strip().lower() for d in (domains or "").split(",") if d.strip()}
        domain = (self.login or "").rpartition("@")[2].lower()
        return bool(domain) and domain in allowed

    def _host_sso_keep_portal_state(self):
        """Write down what the portal user has now, to give it back later."""
        self.ensure_one()
        state = {name: self[name] for name in PORTAL_FIELDS}
        for name in PORTAL_RELATIONS:
            state[name] = self[name].ids
        self.sudo().write(
            {
                "host_sso_user": True,
                "host_sso_adopted": True,
                "host_sso_portal_state": json.dumps(state),
            }
        )

    def _host_sso_give_back(self):
        """Back to the portal user it was before the access."""
        for user in self.sudo():
            state = json.loads(user.host_sso_portal_state or "{}")
            if not state:
                continue
            values = {name: state[name] for name in PORTAL_FIELDS if name in state}
            for name in PORTAL_RELATIONS:
                comodel = user._fields[name].comodel_name
                records = (
                    self.env[comodel].sudo().browse(state.get(name) or []).exists()
                )
                if user._fields[name].type == "many2one":
                    values[name] = records[:1].id or False
                else:
                    values[name] = [Command.set(records.ids)]
            user.with_context(active_test=False).write(values)

    @api.model
    def _host_sso_provision(self, claims):
        """The user of the person in the token, made or reactivated."""
        login = (claims.get("sub") or "").strip().lower()
        if not login:
            raise AccessDenied(_("The entry link does not say who enters."))
        users = self.sudo().with_context(active_test=False, no_reset_password=True)
        user = users.search([("login", "=", login)], limit=1)
        if user and not user.host_sso_user:
            if not user._host_sso_adoptable():
                raise AccessDenied(
                    _(
                        "This instance already has a user %(login)s of its own.",
                        login=login,
                    )
                )
            user._host_sso_keep_portal_state()
        elif user and user.host_sso_adopted and user.share:
            # Back from a previous access: its portal side may have changed.
            user._host_sso_keep_portal_state()
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
        adopted = ended.filtered("host_sso_adopted")
        adopted.filtered(lambda u: not u.share)._host_sso_give_back()
        (ended - adopted).write({"active": False})
        return ended
