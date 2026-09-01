# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo.addons.component.core import Component


class ChannelChannexPmsCancelationRuleExporter(Component):
    _name = "channel.channex.pms.cancelation.rule.exporter"
    _inherit = "channel.channex.exporter"
    _apply_on = "channel.channex.pms.cancelation.rule"

    def _export_dependencies(self):
        """A policy belongs to a property, so the property goes first."""
        self._export_dependency(
            self.binding.backend_id.pms_property_id,
            "channel.channex.pms.property",
        )
        return super()._export_dependencies()
