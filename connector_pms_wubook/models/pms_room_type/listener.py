# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo.addons.component.core import Component
from odoo.addons.component_event.components.event import skip_if

# Fields whose change on the room type warrants a re-export to Wubook.
# These are the ones consumed by the room type export mapper:
# ``name``, ``list_price`` -> ``price``, ``default_code`` -> ``shortname``,
# ``min_price``, ``max_price``, ``default_availability`` -> ``availability``,
# ``class_id`` -> ``rtype``, ``board_service_room_type_ids`` -> ``boards``.
# Also ``room_ids.capacity`` (drives the computed ``occupancy``) is
# relevant, but since ``occupancy`` is stored-and-computed on the binding
# it surfaces here as a write on the binding model rather than on
# ``pms.room.type`` itself; the binding's own listener (inherited from
# base.connector) would handle that branch.
_ROOM_TYPE_RELEVANT_FIELDS = {
    "name",
    "default_code",
    "list_price",
    "min_price",
    "max_price",
    "default_availability",
    "class_id",
    "board_service_room_type_ids",
}


class ChannelWubookPmsRoomTypeListener(Component):
    """Auto-push for room types already bound to one or more Wubook
    backends. Enqueues an ``export_record`` job per binding so the actual
    push is asynchronous (the synchronous variant of
    ``channel.wubook.listener`` is intentionally not used here because it
    would block the user's transaction on every save).

    Filters by relevant fields so an unrelated write (e.g. a Many2many
    update on a side field) does not generate noise on Wubook. Bindings
    without ``external_id`` are skipped.
    """

    _name = "channel.wubook.pms.room.type.listener"
    _inherit = "base.connector.listener"
    _apply_on = "pms.room.type"

    @skip_if(lambda self, record, **kwargs: self.no_connector_export(record))
    def on_record_write(self, record, fields=None):
        if not fields or not (set(fields) & _ROOM_TYPE_RELEVANT_FIELDS):
            return
        for binding in record.channel_wubook_bind_ids:
            if not binding.external_id:
                continue
            binding.with_delay().export_record(binding.backend_id, record)
