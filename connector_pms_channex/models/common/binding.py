# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import fields, models


class ChannelChannexBinding(models.AbstractModel):
    _name = "channel.channex.binding"
    _inherit = "channel.binding"

    # Channex identifies everything by UUID, so the generic Integer is
    # redeclared here rather than on channel.binding: doing it on the abstract
    # base would need a migration of the connector already in production, and
    # every binding of this connector inherits it from here anyway.
    external_id = fields.Char(string="Channex ID", index=True)

    backend_id = fields.Many2one(
        comodel_name="channel.channex.backend",
        string="Channex Backend",
        required=True,
        ondelete="restrict",
    )
