# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import models


class PmsRoom(models.Model):
    """Stash a snapshot of the relevant *before* values into the context
    so the connector listener can walk BOTH the old and the new
    (property, room_type) pairs after a re-segmentation.

    The listener itself fires *after* the write, when ``room_type_id``
    and ``pms_property_id`` already hold the new values. Without this
    snapshot the connector would only push avail for the new pair —
    leaving the OLD room_type's avail on Wubook stale by exactly the
    one room that moved out.
    """

    _inherit = "pms.room"

    _WUBOOK_AVAIL_SNAPSHOT_KEY = "_wubook_room_avail_snapshot"

    def write(self, vals):
        snapshot_fields = {"room_type_id", "pms_property_id"}
        if self and snapshot_fields & set(vals):
            snapshot = {
                r.id: (r.pms_property_id.id, r.room_type_id.id) for r in self
            }
            self = self.with_context(
                **{self._WUBOOK_AVAIL_SNAPSHOT_KEY: snapshot}
            )
        return super().write(vals)
