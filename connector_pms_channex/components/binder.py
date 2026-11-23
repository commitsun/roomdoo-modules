# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo.addons.component.core import AbstractComponent


class ChannelChannexBinder(AbstractComponent):
    _name = "channel.channex.binder"
    _inherit = ["channel.binder", "base.channel.channex.connector"]

    _bind_ids_field = "channel_channex_bind_ids"
