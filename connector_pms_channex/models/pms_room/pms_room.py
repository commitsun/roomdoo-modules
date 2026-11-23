# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import models

from ..common.ari import stage_ari

#: Fields of a room that change how many rooms of a type a property has.
AVAILABILITY_FIELDS = {"active", "room_type_id", "pms_property_id", "parent_id"}


class PmsRoom(models.Model):
    _inherit = "pms.room"

    def _channex_rooms_sharing_availability(self):
        """These rooms, the rooms they are part of and the rooms part of them.

        pms takes all of them off sale when any one is occupied, so a change on
        one of them moves the availability of the room types of all of them.
        """
        rooms = self.browse()
        for room in self:
            rooms |= room
            parent = room.parent_id
            while parent and parent not in rooms:
                rooms |= parent
                parent = parent.parent_id
            children = room.child_ids - rooms
            while children:
                rooms |= children
                children = children.child_ids - rooms
        return rooms

    def _channex_stage_availability(self):
        """Publish again, for the whole window, the room types these rooms
        count towards."""
        for room in self:
            room_types = room._channex_rooms_sharing_availability().room_type_id
            for room_type in room_types:
                stage_ari(
                    self.env,
                    "availability",
                    (room.pms_property_id.id, room_type.id),
                )

    def write(self, vals):
        if AVAILABILITY_FIELDS & set(vals) and not self.env.context.get(
            "connector_no_export"
        ):
            # The listener sees the room where it ends up. The room type and the
            # property it leaves have to be read here, before it moves.
            self._channex_stage_availability()
        return super().write(vals)
