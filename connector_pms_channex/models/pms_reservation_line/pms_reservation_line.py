# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import models

from ..common.ari import stage_ari

#: Fields of a night that move where it counts. ``state`` and
#: ``occupies_availability`` are not here: they are stored computes, written
#: through ``_write()`` where no event is raised, so a cancellation is caught on
#: the reservation instead.
AVAILABILITY_FIELDS = {"room_id", "date", "is_reselling"}

#: Fields of a night that move the quota it spends: the night it is spent on,
#: and the sale channel it is spent through. Its agency and the room type sold
#: are the reservation's.
QUOTA_FIELDS = {"date", "sale_channel_id"}


class PmsReservationLine(models.Model):
    _inherit = "pms.reservation.line"

    def _channex_stage_availability(self):
        """Publish again the availability these nights move.

        A night moves two room types, which are often one and the same: the one
        of the room it occupies, which loses a free room, and the one that was
        sold, whose quota it spends. They differ on an upgrade.
        """
        for line in self:
            room_types = (
                line.room_id._channex_rooms_sharing_availability().room_type_id
                | line.reservation_id.room_type_id
            )
            for room_type in room_types:
                stage_ari(
                    self.env,
                    "availability",
                    (line.pms_property_id.id, room_type.id),
                    line.date,
                    line.date,
                )

    def _channex_stage_channel_caps(self):
        """Publish again what agencies have set aside on these nights.

        A night spends the quota of the property, of its sale channel and of
        its agency all at once, and what is left of any of them may change
        whether an agency is capped lower than the property. Cancelling does not
        give quota back, so only creating, removing or moving a night counts.
        """
        for line in self:
            stage_ari(
                self.env,
                "channel_caps",
                (line.pms_property_id.id, None, line.reservation_id.room_type_id.id),
                line.date,
                line.date,
            )

    def write(self, vals):
        if not self.env.context.get("connector_no_export"):
            # The listener sees the night where it lands. The room and the date
            # it leaves have to be read here, before it moves.
            if AVAILABILITY_FIELDS & set(vals):
                self._channex_stage_availability()
            if QUOTA_FIELDS & set(vals):
                self._channex_stage_channel_caps()
        return super().write(vals)
