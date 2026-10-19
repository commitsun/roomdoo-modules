# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo.addons.component.core import Component


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
