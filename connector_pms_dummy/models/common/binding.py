# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import fields, models


class ChannelDummyBinding(models.AbstractModel):
    _name = "channel.dummy.binding"
    _inherit = "channel.binding"

    # Declared as Char, unlike Wubook's Integer, so the generic layer is
    # exercised against both flavours of external id.
    external_id = fields.Char(string="External ID")

    backend_id = fields.Many2one(
        comodel_name="channel.dummy.backend",
        string="Dummy Backend",
        required=True,
        ondelete="restrict",
    )
