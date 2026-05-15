# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo.addons.component.core import Component
from odoo.addons.component_event.components.event import skip_if

from ..pms_availability.listener import (
    _AVAILABILITY_BUFFER_KEY,
    _flush_availability_buffer,
)

# Fields whose public write on a reservation line shifts the
# availability footprint: ``room_id`` is the actual assignment that
# defines occupancy (and therefore which ``pms.availability`` rows
# move), ``date`` is the night being occupied, and ``is_reselling`` is
# the user-toggleable flag that frees a night without cancelling the
# reservation (occupied → not-occupied through
# ``occupies_availability``'s recompute). ``state`` /
# ``occupies_availability`` are stored computed / related fields and
# are updated through ``_write()`` only — they cannot be observed
# here, which is why the cancel/confirm flow is captured by the
# ``pms.reservation`` listener instead.
_LINE_RELEVANT_FIELDS = {"room_id", "date", "is_reselling"}


class ChannelWubookPmsReservationLineListener(Component):
    """Listener for ``pms.reservation.line``.

    Fires a property-availability re-export on:

    * **create**: a new line means a new night occupied (covers new
      reservations and date extensions of existing ones).
    * **write** of ``room_id`` / ``date``: the line was reassigned to
      a different room or shifted to a different night.
    * **unlink**: a removed line frees the corresponding night
      (covers date reductions).

    Shares the precommit buffer with the avail and reservation
    listeners, so N line events in one transaction collapse to a
    single ``export_record`` job per property binding.
    """

    _name = "channel.wubook.pms.reservation.line.listener"
    _inherit = "base.connector.listener"
    _apply_on = "pms.reservation.line"

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
        room = record.room_id
        room_type = room.room_type_id if room else False
        if not prop or not room_type:
            return
        for property_binding in prop.channel_wubook_bind_ids:
            if not property_binding.external_id:
                continue
            backend = property_binding.backend_id
            room_type_bound = room_type.channel_wubook_bind_ids.filtered(
                lambda b, backend=backend: b.backend_id == backend
                and b.external_id
            )
            if not room_type_bound:
                continue
            self._buffer_property_export(property_binding)

    @skip_if(lambda self, record, **kwargs: self.no_connector_export(record))
    def on_record_create(self, record, fields=None):
        self._enqueue_property_exports(record)

    @skip_if(lambda self, record, **kwargs: self.no_connector_export(record))
    def on_record_write(self, record, fields=None):
        if not fields or not (set(fields) & _LINE_RELEVANT_FIELDS):
            return
        self._enqueue_property_exports(record)

    @skip_if(lambda self, record, **kwargs: self.no_connector_export(record))
    def on_record_unlink(self, record, fields=None):
        self._enqueue_property_exports(record)
