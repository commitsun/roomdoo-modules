# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo.addons.component.core import Component
from odoo.addons.component_event.components.event import skip_if

from ..pms_availability.listener import (
    buffer_property_exports,
    buffer_property_exports_for_rooms,
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

    def _enqueue_property_exports(self, record):
        buffer_property_exports_for_rooms(
            self.env, record.pms_property_id, record.room_id.room_type_id
        )

    @skip_if(lambda self, record, **kwargs: self.no_connector_export(record))
    def on_record_create(self, record, fields=None):
        self._enqueue_property_exports(record)

    @skip_if(lambda self, record, **kwargs: self.no_connector_export(record))
    def on_record_write(self, record, fields=None):
        if not fields or not (set(fields) & _LINE_RELEVANT_FIELDS):
            return
        if "room_id" in fields:
            # The line moved between rooms: the availability freed on the
            # room type it left has to be published too, and the record only
            # carries the new one.
            buffer_property_exports(self.env, record.pms_property_id)
            return
        self._enqueue_property_exports(record)

    @skip_if(lambda self, record, **kwargs: self.no_connector_export(record))
    def on_record_unlink(self, record, fields=None):
        self._enqueue_property_exports(record)
