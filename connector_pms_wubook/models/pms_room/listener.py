# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo.addons.component.core import Component
from odoo.addons.component_event.components.event import skip_if

from ..pms_availability.listener import (
    _AVAILABILITY_BUFFER_KEY,
    _flush_availability_buffer,
)
from .pms_room import PmsRoom

# Fields whose change on a ``pms.room`` shifts the effective room count
# for one or more (property, room_type) pairs, and therefore moves
# ``pms.availability.real_avail`` for every future date of those pairs:
#
# * ``active``: deactivating / re-activating a room.
# * ``room_type_id``: re-segmentation (the most subtle one — see the
#   snapshot mechanism in ``pms_room.py`` for why we need the OLD value).
# * ``pms_property_id``: re-assigning a room to a different property.
# * ``parent_id``: the room hierarchy used for "containing" rooms (e.g.
#   a 2-room suite); changing it shifts the parent / child avail couple.
_ROOM_RELEVANT_FIELDS = {
    "active",
    "room_type_id",
    "pms_property_id",
    "parent_id",
}


class ChannelWubookPmsRoomListener(Component):
    """Listener for ``pms.room``.

    Bridges the room-level changes (activate / deactivate, re-segment,
    re-assign property, change parent) into the same precommit
    availability buffer used by the reservation / line / avail listeners.
    On flush, the buffer enqueues ONE ``export_record`` job per affected
    property binding regardless of how many room rows changed.

    The actual ``real_avail`` recomputation for the affected
    (property, room_type) pairs happens in ``pms`` itself (see
    ``pms.room.write`` / ``create`` / ``unlink`` overrides which call
    ``pms.availability._recompute_real_avail_for_room_change``). That
    recompute writes through ``_write`` and so does NOT fire the avail
    listener — this listener is the only path that schedules the
    follow-up Wubook push.
    """

    _name = "channel.wubook.pms.room.listener"
    _inherit = "base.connector.listener"
    _apply_on = "pms.room"

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

    def _affected_pairs(self, record):
        """Return the set of ``(property_id, room_type_id)`` pairs whose
        Wubook avail may have moved as a consequence of the change.

        For a re-segmentation we include BOTH the old and the new pair
        (the OLD comes from the snapshot stashed by
        ``pms_room.py::write``). For a simple ``active`` toggle or a
        ``parent_id`` change, only the current pair is needed.
        """
        pairs = set()
        prop = record.pms_property_id
        rt = record.room_type_id
        if prop and rt:
            pairs.add((prop.id, rt.id))
        snapshot = self.env.context.get(PmsRoom._WUBOOK_AVAIL_SNAPSHOT_KEY)
        if snapshot:
            old = snapshot.get(record.id)
            if old and old[0] and old[1]:
                pairs.add(old)
        return pairs

    def _enqueue_for_room(self, record):
        pairs = self._affected_pairs(record)
        if not pairs:
            return
        # Index room_types by id for cheap lookup.
        rt_ids = {rt_id for _prop_id, rt_id in pairs}
        room_types = self.env["pms.room.type"].browse(rt_ids).exists()
        prop_ids = {prop_id for prop_id, _rt_id in pairs}
        properties = self.env["pms.property"].browse(prop_ids).exists()
        for prop in properties:
            for property_binding in prop.channel_wubook_bind_ids:
                if not property_binding.external_id:
                    continue
                backend = property_binding.backend_id
                # Only push if at least one of the affected room_types
                # is also bound on this backend — otherwise Wubook
                # cannot apply the change anyway.
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
    def on_record_create(self, record, fields=None):
        self._enqueue_for_room(record)

    @skip_if(lambda self, record, **kwargs: self.no_connector_export(record))
    def on_record_write(self, record, fields=None):
        if not fields or not (set(fields) & _ROOM_RELEVANT_FIELDS):
            return
        self._enqueue_for_room(record)

    @skip_if(lambda self, record, **kwargs: self.no_connector_export(record))
    def on_record_unlink(self, record, fields=None):
        self._enqueue_for_room(record)
