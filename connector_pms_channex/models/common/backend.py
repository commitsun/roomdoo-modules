# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

import logging
import urllib.parse
import uuid

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)

API_PATH = "/api/v1"
STAGING_URL = "https://staging.channex.io" + API_PATH
PRODUCTION_URL = "https://channex.io" + API_PATH

# Languages the embedded Channex UI ships. Anything else falls back to English,
# which is what Channex does anyway, only without leaving a wrong hint in the URL.
UI_LANGUAGES = ("de", "el", "en", "es", "hu", "it", "pt", "ru", "th")


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
    channel_ids = fields.One2many(
        comodel_name="channel.channex.channel",
        inverse_name="backend_id",
        string="Channels",
    )
    booking_revision_ids = fields.One2many(
        comodel_name="channel.channex.booking.revision",
        inverse_name="backend_id",
        string="Booking revisions",
    )
    unmapped_channel_count = fields.Integer(
        compute="_compute_unmapped_channel_count",
        help="Channels with no agency. Their bookings would come in with no "
        "partner to attribute them to.",
    )

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

    # -- embedded Channex UI -----------------------------------------------

    def _channex_web_url(self):
        """``url`` addresses the API; the embedded UI hangs off the web root."""
        self.ensure_one()
        root = (self.url or "").rstrip("/")
        if root.endswith(API_PATH):
            root = root[: -len(API_PATH)]
        return root

    def _channex_property_external_id(self):
        """Channex scopes the embedded session to one of its properties, so the
        property has to be there before anything can be shown inside it."""
        self.ensure_one()
        binding = (
            self.env["channel.channex.pms.property"]
            .with_context(active_test=False)
            .search([("backend_id", "=", self.id)], limit=1)
        )
        if not binding.external_id:
            raise UserError(
                _("Export %s to Channex before opening its Channex screens.")
                % self.pms_property_id.display_name
            )
        return binding.external_id

    def _channex_one_time_token(self, property_id, group_id):
        """Mint a token for one load of the embedded UI.

        It is never stored: Channex drops it on first use and after 15 minutes,
        while the session it opens does not expire once the frame has loaded.
        """
        self.ensure_one()
        body = self._channex_request(
            "POST",
            "auth/one_time_token",
            payload={
                "one_time_token": {
                    "property_id": property_id,
                    "group_id": group_id,
                    "username": self.env.user.name,
                }
            },
        )
        if body is None:
            raise UserError(
                _(
                    "Exports are disabled on this backend, so Channex would not "
                    "authorise the session."
                )
            )
        token = ((body or {}).get("data") or {}).get("token")
        if not token:
            raise UserError(_("Channex did not return an access token."))
        return token

    def channex_iframe_url(self, page="/channels"):
        """URL of a Channex screen, embeddable in Odoo.

        Called by the client action on every mount rather than handed over once
        in the action, because the token only survives a single load and an
        action lives on in the breadcrumb.
        """
        self.ensure_one()
        property_id = self._channex_property_external_id()
        group_id = self._channex_group_id()
        lang = (self.env.user.lang or "en").split("_")[0]
        query = urllib.parse.urlencode(
            {
                "oauth_session_key": self._channex_one_time_token(
                    property_id, group_id
                ),
                # Hides the Channex chrome, so what is left is the screen itself.
                "app_mode": "headless",
                "redirect_to": page,
                "property_id": property_id,
                "group_id": group_id,
                "lng": lang if lang in UI_LANGUAGES else "en",
            }
        )
        return f"{self._channex_web_url()}/auth/exchange?{query}"

    # -- channels ----------------------------------------------------------

    @api.depends("channel_ids.agency_id", "channel_ids.active")
    def _compute_unmapped_channel_count(self):
        for rec in self:
            # ``active`` is filtered here and not left to ``active_test``: a
            # channel deactivated during the sync is still in the one2many that
            # was read before it, and a channel that no longer exists on Channex
            # is nothing to warn about.
            rec.unmapped_channel_count = len(
                rec.channel_ids.filtered(
                    lambda channel: channel.active and not channel.agency_id
                )
            )

    def _channex_fetch_channels(self):
        """The channels of this backend's property, as Channex reports them.

        An account holds the channels of all its properties, so the property
        filter is the scoping, not an optimisation.
        """
        self.ensure_one()
        property_id = self._channex_property_external_id()
        with self.work_on("channel.channex.channel") as work:
            adapter = work.component(usage="backend.adapter")
            return adapter.search_read([("property_id", "=", property_id)])

    def channex_sync_channels(self):
        """Bring in the channels the hotel connected on Channex.

        Nothing is created on Channex from here, and no partner is invented
        either: an unmapped channel is reported, never guessed.

        There is no button for this. The screen that guides the hotel through
        connecting channels runs it on its own, because a step called
        "synchronise" is a step that means nothing to a hotelier.
        """
        self.ensure_one()
        channels = self.env["channel.channex.channel"]
        seen = channels
        for values in self._channex_fetch_channels():
            seen |= channels._channex_upsert(self, values)
        gone = self.channel_ids - seen
        gone.write({"active": False})
        return {"total": len(seen), "unmapped": self.unmapped_channel_count}

    def action_open_channex_channels(self):
        """The whole channel flow, in one screen.

        Meant to be opened from anywhere -- today a button on this form, later a
        hotel facing dashboard -- so everything it needs is the backend and the
        step to land on. It starts on the mapping step once there is something
        to map, which is what makes it a screen a hotel can come back to rather
        than a one-shot wizard.
        """
        self.ensure_one()
        return {
            "type": "ir.actions.client",
            "tag": "channex_channels",
            "name": _("Channels"),
            "params": {
                "backend_id": self.id,
                "step": "map" if self.channel_ids else "connect",
            },
        }

    # -- bookings ----------------------------------------------------------

    def _channex_booking_revision_feed(self):
        """The messages Channex is holding for this property, oldest first."""
        self.ensure_one()
        property_id = self._channex_property_external_id()
        with self.work_on("channel.channex.booking.revision") as work:
            return work.component(usage="backend.adapter").feed(property_id)

    def channex_import_booking_revisions(self):
        """Read the feed and queue what to make of each message.

        One job per message, so each gets a transaction of its own: a message
        pms refuses cannot take the rest of the read down with it, and its
        payload travels in the job, which is what makes retrying one possible
        even after Channex stops offering it.

        Nothing is acknowledged here either. That is a job of its own, and it
        only runs once the folio it confirms is saved for good.
        """
        self.ensure_one()
        payloads = self._channex_booking_revision_feed()
        queued = self.env["channel.channex.booking.revision"]._channex_schedule(
            self, payloads
        )
        self.channex_acknowledge_booking_revisions()
        return {"total": len(payloads), "queued": queued}

    def channex_acknowledge_booking_revisions(self):
        """Queue the receipt of every message settled and not yet confirmed.

        Asked of the whole backlog and not only of this read: a receipt that
        could not be sent -- exports disabled on this backend, Channex down --
        is simply still pending, and the next read picks it up.
        """
        self.ensure_one()
        pending = self.env["channel.channex.booking.revision"].search(
            [
                ("backend_id", "=", self.id),
                ("acknowledged_at", "=", False),
                ("state", "in", ("applied", "superseded")),
            ]
        )
        pending._channex_schedule_acknowledge()
        return len(pending)

    def action_import_booking_revisions(self):
        self.ensure_one()
        result = self.channex_import_booking_revisions()
        return self._notify(
            _("%(queued)s of %(total)s booking message(s) queued.") % result
        )

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
