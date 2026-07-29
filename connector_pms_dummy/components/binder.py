# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo.addons.component.core import AbstractComponent, Component


class ChannelDummyBinder(AbstractComponent):
    _name = "channel.dummy.binder"
    _inherit = ["channel.binder", "base.channel.dummy.connector"]

    _bind_ids_field = "channel_dummy_bind_ids"


class ChannelDummyPmsRoomTypeBinder(Component):
    _name = "channel.dummy.pms.room.type.binder"
    _inherit = "channel.dummy.binder"
    _apply_on = "channel.dummy.pms.room.type"


class ChannelDummyProductPricelistBinder(Component):
    _name = "channel.dummy.product.pricelist.binder"
    _inherit = "channel.dummy.binder"
    _apply_on = "channel.dummy.product.pricelist"
