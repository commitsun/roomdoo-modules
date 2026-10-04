# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo.addons.component.core import Component


class ChannelChannexPmsPropertyExporter(Component):
    _name = "channel.channex.pms.property.exporter"
    _inherit = "channel.channex.exporter"
    _apply_on = "channel.channex.pms.property"
