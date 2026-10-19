# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo.addons.component.core import AbstractComponent


class ChannelChannexImporter(AbstractComponent):
    _name = "channel.channex.importer"
    _inherit = ["channel.importer", "base.channel.channex.connector"]
