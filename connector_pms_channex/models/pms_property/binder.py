# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo.addons.component.core import Component


class ChannelChannexPmsPropertyBinder(Component):
    _name = "channel.channex.pms.property.binder"
    _inherit = "channel.channex.binder"
    _apply_on = "channel.channex.pms.property"
