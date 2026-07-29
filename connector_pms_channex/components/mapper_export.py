# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo.addons.component.core import AbstractComponent


class ChannelChannexMapperExport(AbstractComponent):
    _name = "channel.channex.export.mapper"
    _inherit = ["channel.mapper.export", "base.channel.channex.connector"]


class ChannelChannexChildMapperExport(AbstractComponent):
    _name = "channel.channex.child.mapper.export"
    _inherit = ["channel.child.mapper.export", "base.channel.channex.connector"]
