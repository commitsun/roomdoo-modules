# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import fields, models


class PmsRoomType(models.Model):
    _name = "pms.room.type"
    _inherit = "pms.room.type"

    channel_channex_bind_ids = fields.One2many(
        comodel_name="channel.channex.pms.room.type",
        inverse_name="odoo_id",
        string="Channel Channex Bindings",
    )
