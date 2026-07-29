# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import fields, models


class ChannelDummyPmsRoomType(models.Model):
    _name = "channel.dummy.pms.room.type"
    _inherit = "channel.dummy.binding"
    _inherits = {"pms.room.type": "odoo_id"}
    _description = "Channel Dummy PMS Room Type"

    odoo_id = fields.Many2one(
        comodel_name="pms.room.type",
        string="Room Type",
        required=True,
        ondelete="cascade",
    )


class PmsRoomType(models.Model):
    _name = "pms.room.type"
    _inherit = "pms.room.type"

    channel_dummy_bind_ids = fields.One2many(
        comodel_name="channel.dummy.pms.room.type",
        inverse_name="odoo_id",
        string="Channel Dummy PMS Bindings",
    )
