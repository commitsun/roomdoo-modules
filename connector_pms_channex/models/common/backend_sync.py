# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

import datetime

from odoo import _, fields, models
from odoo.exceptions import UserError
from odoo.tools.misc import format_datetime

from odoo.addons.queue_job.job import identity_exact

#: How often Channex takes a full sync of a property in production. Its
#: certification asks for no more, and checks.
FULL_SYNC_INTERVAL = datetime.timedelta(hours=24)


class ChannelChannexBackend(models.Model):
    _inherit = "channel.channex.backend"

    channex_full_sync_date = fields.Datetime(
        string="Last synchronised",
        readonly=True,
        copy=False,
        help="When everything was last sent to Channex. In production Channex "
        "takes it once a day at most.",
    )

    def action_channex_sync_all(self):
        """Bring Channex in line with Odoo for this property, all of it.

        What setting a property going takes, and what puts it right after it
        drifted: the property, its room types, the rate plans of the pricelists
        sold, the channels connected on Channex, and then every night of
        availability, rates and restrictions -- the full sync Channex's
        certification asks for. Last, the bookings Channex is holding are read.

        The masters are brought in line here and now, so that whatever Channex
        refuses is said on the screen and nothing is published on top of it.
        The rest is queued, and only goes once all this is saved.
        """
        self.ensure_one()
        if self.export_disabled:
            raise UserError(_("Exports are disabled on this backend."))
        # Never made from here: the property may already be on Channex, and
        # connecting it is where the hotel says whether to take that one over.
        if not self._channex_bound_property_id():
            raise UserError(
                _(
                    "Connect %s to Channex first, from the Connectors tab of the "
                    "property."
                )
                % self.pms_property_id.display_name
            )
        self._channex_check_full_sync()
        self.env["channel.channex.pms.property"].export_record(
            self, self.pms_property_id
        )
        self._channex_sync_room_types()
        self.channex_sync_rate_plans()
        self.channex_sync_channels()
        self._channex_publish_everything()
        self.with_delay(identity_key=identity_exact).channex_import_booking_revisions()
        self.channex_full_sync_date = fields.Datetime.now()
        return self._notify(
            _("Everything about %s is on its way to Channex.")
            % self.pms_property_id.display_name
        )

    def _channex_check_full_sync(self):
        """Refuse a second full sync within a day, in production.

        Only there: staging is where it is tried over and over, and nothing is
        sold through it.
        """
        self.ensure_one()
        last = self.channex_full_sync_date
        if self.environment != "production" or not last:
            return
        allowed_from = last + FULL_SYNC_INTERVAL
        if fields.Datetime.now() < allowed_from:
            raise UserError(
                _(
                    "Everything was last sent to Channex on %(last)s. Channex "
                    "takes it once a day at most, so it can be sent again from "
                    "%(next)s. What changes in the meantime is sent on its own."
                )
                % {
                    "last": format_datetime(self.env, last),
                    "next": format_datetime(self.env, allowed_from),
                }
            )

    def _channex_sync_room_types(self):
        """Every room type the property has rooms of, and every one this
        backend carries, brought in line with what it should be: created,
        updated or taken down."""
        self.ensure_one()
        room_types = (
            self.env["pms.room"]
            .search([("pms_property_id", "=", self.pms_property_id.id)])
            .room_type_id
        )
        carried = (
            self.env["channel.channex.pms.room.type"]
            .with_context(active_test=False)
            .search([("backend_id", "=", self.id)])
            .odoo_id
        )
        # Searched with the archived ones, worked out without: an archived room
        # of the type would otherwise still count as one it has here.
        for room_type in room_types | carried.with_env(self.env):
            room_type._channex_reconcile(self)
