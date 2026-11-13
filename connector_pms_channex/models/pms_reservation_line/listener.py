# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo.addons.component.core import Component
from odoo.addons.component_event.components.event import skip_if

from .pms_reservation_line import AVAILABILITY_FIELDS


class ChannelChannexPmsReservationLineListener(Component):
    """A night created, moved or removed changes what is left to sell.

    Creating covers new reservations and longer stays, removing covers shorter
    ones; a night moved to another room or date is published where it lands
    here, and where it left from in ``write``.
    """

    _name = "channel.channex.pms.reservation.line.listener"
    _inherit = "base.connector.listener"
    _apply_on = "pms.reservation.line"

    @skip_if(lambda self, record, **kwargs: self.no_connector_export(record))
    def on_record_create(self, record, fields=None):
        record._channex_stage_availability()

    @skip_if(lambda self, record, **kwargs: self.no_connector_export(record))
    def on_record_write(self, record, fields=None):
        if fields and set(fields) & AVAILABILITY_FIELDS:
            record._channex_stage_availability()

    @skip_if(lambda self, record, **kwargs: self.no_connector_export(record))
    def on_record_unlink(self, record, fields=None):
        # Fired before the delete, the only moment the night can still be read.
        record._channex_stage_availability()
