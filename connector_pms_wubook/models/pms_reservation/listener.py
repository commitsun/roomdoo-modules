# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo.addons.component.core import Component
from odoo.addons.component_event.components.event import skip_if

from ..pms_availability.listener import (
    _AVAILABILITY_BUFFER_KEY,
    _flush_availability_buffer,
)

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
# ``preferred_room_id`` / ``room_type_id`` are header fields whose write
# REASSIGNS the reservation to a different room: that only RECOMPUTES
# ``pms.reservation.line.room_id`` (a stored compute) through Odoo's
# internal ``_write()``, which ``component_event`` does NOT observe, so
# the ``pms.reservation.line`` listener never fires. Without catching
# them here the new occupancy is never pushed to Wubook — the bug being
# fixed: a reservation moved (via the header) into a room of a DIFFERENT
# room_type left that room_type still sellable on the channel.
#
# Date changes still propagate as line create / unlink (public writes)
# and remain caught by the ``pms.reservation.line`` listener.
_RESERVATION_RELEVANT_FIELDS = {"state", "preferred_room_id", "room_type_id"}


class ChannelWubookPmsReservationListener(Component):
    """State-change listener for ``pms.reservation``.

    Coalesces through the same precommit buffer as the avail / line
    listeners so a folio-wide cancel collapses to ONE
    ``export_record`` job per affected property binding.
    """

    _name = "channel.wubook.pms.reservation.listener"
    _inherit = "base.connector.listener"
    _apply_on = "pms.reservation"

    def _buffer_property_export(self, property_binding):
        cr = self.env.cr
        data = cr.precommit.data
        if _AVAILABILITY_BUFFER_KEY not in data:
            data[_AVAILABILITY_BUFFER_KEY] = {}
            env = self.env
            cr.precommit.add(
                lambda env=env: _flush_availability_buffer(env)
            )
        data[_AVAILABILITY_BUFFER_KEY].setdefault(
            property_binding.id, property_binding
        )

    def _enqueue_property_exports(self, record):
        prop = record.pms_property_id
        if not prop:
            return
        # The availability footprint is defined by the rooms actually
        # assigned to the reservation lines — NOT by the reservation
        # header's preferred ``room_type_id``.
        room_types = record.reservation_line_ids.mapped(
            "room_id.room_type_id"
        )
        if not room_types:
            return
        for property_binding in prop.channel_wubook_bind_ids:
            if not property_binding.external_id:
                continue
            backend = property_binding.backend_id
            bound = room_types.filtered(
                lambda rt, backend=backend: any(
                    b.backend_id == backend and b.external_id
                    for b in rt.channel_wubook_bind_ids
                )
            )
            if not bound:
                continue
            self._buffer_property_export(property_binding)

    @skip_if(lambda self, record, **kwargs: self.no_connector_export(record))
    def on_record_write(self, record, fields=None):
        if not fields or not (set(fields) & _RESERVATION_RELEVANT_FIELDS):
            return
        self._enqueue_property_exports(record)
