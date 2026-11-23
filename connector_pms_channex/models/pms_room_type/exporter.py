# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo.addons.component.core import Component

from ..common.ari import stage_ari


class ChannelChannexPmsRoomTypeExporter(Component):
    _name = "channel.channex.pms.room.type.exporter"
    _inherit = "channel.channex.exporter"
    _apply_on = "channel.channex.pms.room.type"

    def _has_to_skip(self):
        """A room type of an excluded class is never sent, and neither is one
        with no rooms: Channex rejects ``count_of_rooms`` below 1."""
        if self.binding._is_excluded():
            return True
        if self.binding.count_of_rooms < 1:
            return True
        return super()._has_to_skip()

    def _export_dependencies(self):
        """Channex needs the property to exist before its room types."""
        self._export_dependency(
            self.binding.backend_id.pms_property_id,
            "channel.channex.pms.property",
        )
        return super()._export_dependencies()

    def _after_export(self):
        """A room type that has just reached Channex has no availability there
        yet, and one whose rooms changed has a different one: either way, the
        whole window of it is published again. One that has just reached it has
        no rate plans either."""
        if self.binding.external_id:
            stage_ari(
                self.env,
                "availability",
                (self.backend_record.pms_property_id.id, self.binding.odoo_id.id),
            )
            self.backend_record._channex_schedule_rate_plans()
            # Its rooms may hold a different number of adults now: a price for
            # each of them, and the occupancy its price is given for, which
            # follows the smallest room.
            self.binding.odoo_id._channex_stage_restrictions(
                self.backend_record.pms_property_id.id
            )
        return super()._after_export()
