# Copyright 2024 Commit [Sun]
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import fields, models


class PmsReservationLine(models.Model):
    _inherit = "pms.reservation.line"

    def write(self, vals):
        # Capture old room data before writing, for lines that may trigger changeroom
        room_changes = []
        skip = self.env.context.get("televes_skip")
        if "room_id" in vals and not skip:
            today = fields.Date.today()
            for line in self:
                if (
                    line.date
                    and line.date == today
                    and line.reservation_id.state == "onboard"
                    and line.reservation_id.televes_checkin_sent
                    and line.room_id
                ):
                    room_changes.append((line.reservation_id, line.room_id))

        result = super().write(vals)

        if room_changes:
            new_room = self.env["pms.room"].browse(vals["room_id"])
            for reservation, old_room in room_changes:
                if old_room != reservation.televes_current_room_id:
                    # Already updated by a previous line in same write; skip
                    continue
                if old_room.id != vals["room_id"]:
                    property_id = reservation.pms_property_id
                    property_id._televes_send_changeroom(
                        reservation, old_room, new_room
                    )
                    reservation.televes_current_room_id = new_room

        return result
