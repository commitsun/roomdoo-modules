# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo.addons.component.core import Component
from odoo.addons.component_event.components.event import skip_if

from .pms_room import AVAILABILITY_FIELDS

# Fields of a room that move what Channex has to know about its room type:
# ``count_of_rooms`` and the occupancies are derived from the rooms of the type
# in the backend's property.
_ROOM_RELEVANT_FIELDS = {
    "active",
    "capacity",
    "pms_property_id",
    "room_type_id",
}


class ChannelChannexPmsRoomListener(Component):
    """A room type only exists on Channex while it has rooms, because Channex
    rejects ``count_of_rooms`` below 1. So the rooms, not the room type, are
    what actually creates and removes it there: a brand new room type has no
    rooms yet and cannot be sent.

    A room is also what the availability of its room type is counted in, so
    adding, removing or moving one publishes that availability again.
    """

    _name = "channel.channex.pms.room.listener"
    _inherit = "base.connector.listener"
    _apply_on = "pms.room"

    @skip_if(lambda self, record, **kwargs: self.no_connector_export(record))
    def on_record_create(self, record, fields=None):
        record.room_type_id._channex_schedule_sync()
        record._channex_stage_availability()

    @skip_if(lambda self, record, **kwargs: self.no_connector_export(record))
    def on_record_write(self, record, fields=None):
        if not fields:
            return
        if set(fields) & AVAILABILITY_FIELDS:
            record._channex_stage_availability()
        if set(fields) & _ROOM_RELEVANT_FIELDS:
            record.room_type_id._channex_schedule_sync()

    @skip_if(lambda self, record, **kwargs: self.no_connector_export(record))
    def on_record_unlink(self, record, fields=None):
        # Fired before the delete, so the room still counts here. The job runs
        # after the commit and sees the room gone.
        record.room_type_id._channex_schedule_sync()
        record._channex_stage_availability()
