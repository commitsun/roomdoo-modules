# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import fields, models


class ChannelDummyProductPricelist(models.Model):
    _name = "channel.dummy.product.pricelist"
    _inherit = "channel.dummy.binding"
    _inherits = {"product.pricelist": "odoo_id"}
    _description = "Channel Dummy Product Pricelist"

    odoo_id = fields.Many2one(
        comodel_name="product.pricelist",
        string="Pricelist",
        required=True,
        ondelete="cascade",
    )


class ProductPricelist(models.Model):
    _name = "product.pricelist"
    _inherit = "product.pricelist"

    channel_dummy_bind_ids = fields.One2many(
        comodel_name="channel.dummy.product.pricelist",
        inverse_name="odoo_id",
        string="Channel Dummy PMS Bindings",
    )
