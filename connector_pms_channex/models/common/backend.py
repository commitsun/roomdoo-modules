# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

import logging
import uuid

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)

STAGING_URL = "https://staging.channex.io/api/v1"
PRODUCTION_URL = "https://channex.io/api/v1"


class ChannelChannexBackend(models.Model):
    _name = "channel.channex.backend"
    _inherit = "connector.backend"
    _inherits = {"channel.backend": "parent_id"}
    _description = "Channel Channex Backend"
    _check_pms_properties_auto = True

    parent_id = fields.Many2one(
        comodel_name="channel.backend",
        string="Parent Channel Backend",
        required=True,
        ondelete="cascade",
    )

    _sql_constraints = [
        (
            "backend_parent_uniq",
            "unique(parent_id)",
            "Only one backend child is allowed for each generic backend.",
        ),
    ]

    environment = fields.Selection(
        selection=[
            ("staging", "Staging"),
            ("production", "Production"),
        ],
        default="staging",
        required=True,
    )
    url = fields.Char(
        compute="_compute_url",
        store=True,
        readonly=False,
        help="Overridable, but it normally follows the environment.",
    )
    api_key = fields.Char(
        string="API key",
        required=True,
        groups="connector.group_connector_manager",
    )
    timeout_connect = fields.Integer(default=5, required=True)
    timeout_read = fields.Integer(default=30, required=True)
    page_limit = fields.Integer(
        default=100,
        required=True,
        help="Items per page when listing. Channex defaults to 10.",
    )
    group_id = fields.Char(
        string="Channex group ID",
        help="Channex requires every property to belong to a group. It lives "
        "here and not on the backend type because a group is an id inside a "
        "Channex account, and the account is the one the API key belongs to.",
    )
    group_title = fields.Char(string="Channex group name", readonly=True)

    @api.depends("environment")
    def _compute_url(self):
        for rec in self:
            rec.url = PRODUCTION_URL if rec.environment == "production" else STAGING_URL

    def action_test_connection(self):
        """Cheapest authenticated call: one page of one property."""
        self.ensure_one()
        self._channex_request("GET", "properties", params={"pagination[limit]": 1})
        return self._notify(_("Connected to %s.") % self.url)

    @api.constrains("group_id")
    def _check_group_id(self):
        """Catch a group id pasted from the Channex panel URL, which carries a
        trailing path segment and is only rejected later, at export time."""
        for rec in self:
            if not rec.group_id:
                continue
            try:
                uuid.UUID(rec.group_id)
            except (ValueError, AttributeError, TypeError) as e:
                raise ValidationError(
                    _("%s is not a Channex group ID. It has to be a plain UUID.")
                    % rec.group_id
                ) from e

    def _channex_group_id(self):
        """Channex requires every property to belong to a group."""
        self.ensure_one()
        if not self.group_id:
            raise UserError(
                _(
                    "This backend has no Channex group yet. Fetch or create one "
                    "before exporting a property."
                )
            )
        return self.group_id

    # -- group setup -------------------------------------------------------

    def _channex_request(self, *args, **kwargs):
        """A call not tied to any entity. The adapter stays inside ``work_on``,
        which is a context manager a connector may use to close resources."""
        self.ensure_one()
        with self.work_on("channel.channex.pms.property") as work:
            return work.component(usage="backend.adapter").request(*args, **kwargs)

    def _fetch_channex_groups(self):
        """The groups the API key gives access to, as ``(id, title)`` pairs."""
        self.ensure_one()
        body = self._channex_request(
            "GET", "groups", params={"pagination[limit]": self.page_limit}
        )
        return [
            (data.get("id"), (data.get("attributes") or {}).get("title") or "")
            for data in (body or {}).get("data") or []
            if data.get("id")
        ]

    def action_fetch_channex_group(self):
        """Pick up the account's group. Channex hides its groups screen until
        the account has a property, so a brand new hotel cannot get the id from
        the UI."""
        self.ensure_one()
        groups = self._fetch_channex_groups()
        if not groups:
            raise UserError(
                _("This Channex account has no group. Create one to continue.")
            )
        if len(groups) == 1:
            external_id, title = groups[0]
            self.write({"group_id": external_id, "group_title": title})
            return self._notify(_("Using Channex group %s.") % (title or external_id))
        options = self.env["channel.channex.group.option"].create(
            [
                {"backend_id": self.id, "external_id": external_id, "title": title}
                for external_id, title in groups
            ]
        )
        return {
            "type": "ir.actions.act_window",
            "name": _("Channex groups"),
            "res_model": "channel.channex.group.option",
            "view_mode": "tree",
            "domain": [("id", "in", options.ids)],
            "target": "new",
        }

    def action_create_channex_group(self):
        """Create the group on Channex, so the initial setup is doable from
        Odoo alone."""
        self.ensure_one()
        title = self.group_title or self.pms_property_id.name
        if not title:
            raise UserError(_("Set a group name first."))
        body = self._channex_request(
            "POST", "groups", payload={"group": {"title": title}}
        )
        if body is None:
            raise UserError(
                _("Exports are disabled on this backend, so the group was not created.")
            )
        data = (body or {}).get("data") or {}
        if not data.get("id"):
            raise UserError(_("Channex did not return a group ID."))
        self.write({"group_id": data["id"], "group_title": title})
        return self._notify(_("Channex group %s created.") % title)

    def _notify(self, message):
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {"type": "success", "message": message, "sticky": False},
        }


class ChannelChannexGroupOption(models.TransientModel):
    """One fetched Channex group, offered for selection when the account has
    more than one."""

    _name = "channel.channex.group.option"
    _description = "Channel Channex Group Option"

    backend_id = fields.Many2one(
        comodel_name="channel.channex.backend",
        required=True,
        ondelete="cascade",
    )
    external_id = fields.Char(string="Group ID", required=True, readonly=True)
    title = fields.Char(readonly=True)

    def action_use(self):
        self.ensure_one()
        self.backend_id.write({"group_id": self.external_id, "group_title": self.title})
        return {"type": "ir.actions.act_window_close"}
