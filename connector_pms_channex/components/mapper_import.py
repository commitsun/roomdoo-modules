# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo.addons.component.core import AbstractComponent


class ChannelChannexMapperImport(AbstractComponent):
    _name = "channel.channex.mapper.import"
    _inherit = ["channel.mapper.import", "base.channel.channex.connector"]


class ChannelChannexChildMapperImport(AbstractComponent):
    _name = "channel.channex.child.mapper.import"
    _inherit = ["channel.child.mapper.import", "base.channel.channex.connector"]
