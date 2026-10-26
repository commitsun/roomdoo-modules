# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import models

#: Fields of a reservation that move its nights to other rooms or room types.
#: Moving a reservation from its header recomputes the rooms of its nights
#: through ``_write()``, where no event is raised, so it is caught here.
ROOM_FIELDS = {"preferred_room_id", "room_type_id"}


class PmsReservation(models.Model):
    _inherit = "pms.reservation"

    def _channex_stage_availability(self):
        """Publish again the availability the nights of these reservations
        move, whatever state they are in."""
        self.reservation_line_ids._channex_stage_availability()

    def _channex_moves_availability(self, vals):
        """Whether this write frees or takes rooms.

        A state only matters on the way into or out of cancelled: those are the
        nights that stop or start taking a room. Its other changes -- confirmed,
        checked in, checked out -- leave the room where it was.
        """
        if ROOM_FIELDS & set(vals):
            return True
        if "state" not in vals:
            return False
        return vals["state"] == "cancel" or any(
            reservation.state == "cancel" for reservation in self
        )

    def write(self, vals):
        if not self.env.context.get(
            "connector_no_export"
        ) and self._channex_moves_availability(vals):
            # Where the nights are before the write: the rooms they leave, and
            # for a cancellation the very nights that are freed. Where they end
            # up is published by the listener.
            self._channex_stage_availability()
        return super().write(vals)
