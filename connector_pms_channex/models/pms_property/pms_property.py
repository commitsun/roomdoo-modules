# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import fields, models


class PmsProperty(models.Model):
    _name = "pms.property"
    _inherit = "pms.property"

    channel_channex_bind_ids = fields.One2many(
        comodel_name="channel.channex.pms.property",
        inverse_name="odoo_id",
        string="Channel Channex Bindings",
    )
