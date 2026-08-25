# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo.addons.component.core import Component
from odoo.addons.component_event.components.event import skip_if

from ..pms_availability.listener import buffer_property_exports_for_rooms

# Why this listener exists: when a reservation is cancelled (or
# re-confirmed) the only PUBLIC write Odoo performs is
# ``pms.reservation.state = 'cancel'``. Everything downstream —
# ``pms.reservation.line.state`` (a stored ``related``),
# ``occupies_availability`` (a stored compute), ``pms.availability.real_avail``
# (another stored compute) — is updated through Odoo's internal
# ``_write()`` path which is NOT hooked by ``component_event``. So the
# avail / line listeners would never fire for cancellations. This
# listener catches the state change at the reservation level and walks
# down to the lines to figure out which property bindings need an avail
# re-export.
#
# Other fields (dates, room_type_id, etc.) are NOT listed here: changes
# to those propagate as line creates / unlinks / room_id writes which
# are caught by the ``pms.reservation.line`` listener.
_RESERVATION_RELEVANT_FIELDS = {"state"}


class ChannelWubookPmsReservationListener(Component):
    """State-change listener for ``pms.reservation``.

    Coalesces through the same precommit buffer as the avail / line
    listeners so a folio-wide cancel collapses to ONE
    ``export_record`` job per affected property binding.
    """

    _name = "channel.wubook.pms.reservation.listener"
    _inherit = "base.connector.listener"
    _apply_on = "pms.reservation"

    def _enqueue_property_exports(self, record):
        # The availability footprint is defined by the rooms actually
        # assigned to the reservation lines — NOT by the reservation
        # header's preferred ``room_type_id``.
        buffer_property_exports_for_rooms(
            self.env,
            record.pms_property_id,
            record.reservation_line_ids.mapped("room_id.room_type_id"),
        )

    @skip_if(lambda self, record, **kwargs: self.no_connector_export(record))
    def on_record_write(self, record, fields=None):
        if not fields or not (set(fields) & _RESERVATION_RELEVANT_FIELDS):
            return
        self._enqueue_property_exports(record)
