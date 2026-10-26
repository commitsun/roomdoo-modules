# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo.addons.component.core import Component
from odoo.addons.component_event.components.event import skip_if

from .pms_reservation import QUOTA_FIELDS, ROOM_FIELDS


class ChannelChannexPmsReservationListener(Component):
    """A reservation moved to another room or room type publishes its nights
    where they land, and one moved to another agency or room type sold publishes
    the quota they now spend. Where they left from, and a cancellation, are
    published from ``write``, which still sees them as they were."""

    _name = "channel.channex.pms.reservation.listener"
    _inherit = "base.connector.listener"
    _apply_on = "pms.reservation"

    @skip_if(lambda self, record, **kwargs: self.no_connector_export(record))
    def on_record_write(self, record, fields=None):
        if fields and set(fields) & ROOM_FIELDS:
            record._channex_stage_availability()
        if fields and set(fields) & QUOTA_FIELDS:
            record._channex_stage_channel_caps()
