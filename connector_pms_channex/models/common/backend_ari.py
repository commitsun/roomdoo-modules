# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

import datetime

import pytz

from odoo import _, api, fields, models

from odoo.addons.queue_job.exception import RetryableJobError

from .ari import ARI_BINDING_MODELS, ARI_DELAY, stage_ari

#: How many days Channex publishes when the property binding says nothing else.
DEFAULT_STATE_LENGTH = 500

#: Channex moves on to the next operational day at 02:00 property time, so
#: until then the night that started yesterday is still on sale.
OPERATIONAL_DAY_SHIFT = datetime.timedelta(hours=2)

#: Nights at the end of the window published again every day. A night enters
#: the window every day with nothing having changed on it, so no change would
#: ever publish it; a few rather than one, so a day the cron did not run is
#: caught up the next.
HORIZON_TOPUP_NIGHTS = 7


class ChannelChannexBackend(models.Model):
    _inherit = "channel.channex.backend"

    channex_ari_warning_ids = fields.One2many(
        comodel_name="channel.channex.ari.warning",
        inverse_name="backend_id",
        string="Refused by Channex",
    )

    def _channex_ari_horizon(self):
        """First and last night Channex publishes for this property.

        In the hotel's calendar and not the server's: Channex refuses a past
        date, and the server's date can be ahead of the hotel's or behind it.
        """
        self.ensure_one()
        tz = pytz.timezone(self.pms_property_id.tz or "UTC")
        first = (datetime.datetime.now(tz) - OPERATIONAL_DAY_SHIFT).date()
        state_length = (
            self.env["channel.channex.pms.property"]
            .with_context(active_test=False)
            .search([("backend_id", "=", self.id)], limit=1)
            .state_length
        ) or DEFAULT_STATE_LENGTH
        return first, first + datetime.timedelta(days=state_length - 1)

    def _channex_schedule_ari(self, kind):
        """Queue the send of a kind, unless one is already waiting."""
        self.ensure_one()
        self.sudo().with_delay(
            eta=ARI_DELAY,
            identity_key=f"channex_ari:{self.id}:{kind}",
            description=_("Channex: send %(kind)s of %(property)s")
            % {"kind": kind, "property": self.pms_property_id.display_name},
        ).channex_send_ari(kind)

    def channex_send_ari(self, kind):
        """Publish everything of a kind this backend has pending. Job entry point.

        The identity key keeps a second job from being queued while one is
        waiting, but not while one is running: a send that starts while another
        is still talking to Channex steps back and tries again in a moment, so
        two sends of the same kind never overlap. Stepping back does not spend
        a retry, because waiting for a turn is not a failure.
        """
        self.ensure_one()
        self.env.cr.execute(
            "SELECT pg_try_advisory_xact_lock(hashtext(%s))",
            (f"channex_ari:{self.id}:{kind}",),
        )
        if not self.env.cr.fetchone()[0]:
            raise RetryableJobError(
                _("Another job is sending %s to Channex for this property.") % kind,
                seconds=ARI_DELAY,
                ignore_retry=True,
            )
        with self.work_on(ARI_BINDING_MODELS[kind]) as work:
            return work.component(usage="ari.sender").run()

    def action_channex_publish_availability(self):
        """Publish the availability of every room type, for the whole window.

        What Channex calls a full sync: the way to set a property going, or to
        put it right after it drifted. Not something to schedule -- Channex
        allows it once a day at most, and only when needed.
        """
        self.ensure_one()
        stage_ari(self.env, "availability", (self.pms_property_id.id, None))
        # What the agencies have set aside is availability too, by channel. Only
        # the rules that differ from what Channex has are sent.
        stage_ari(self.env, "channel_caps", (self.pms_property_id.id, None, None))
        return self._notify(
            _("The availability of every room type is queued for Channex.")
        )

    @api.model
    def channex_cron_ari_horizon(self):
        """Publish the nights that have just entered the window. Entry point of
        the scheduled action."""
        for backend in self.search([]):
            if not backend._channex_bound_property_id():
                continue
            last = backend._channex_ari_horizon()[1]
            first_new = last - datetime.timedelta(days=HORIZON_TOPUP_NIGHTS - 1)
            property_id = backend.pms_property_id.id
            stage_ari(self.env, "availability", (property_id, None), first_new, last)
            stage_ari(
                self.env, "channel_caps", (property_id, None, None), first_new, last
            )
        return True
