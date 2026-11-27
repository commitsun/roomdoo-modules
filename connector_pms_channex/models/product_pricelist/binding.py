# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import api, fields, models


class ChannelChannexProductPricelist(models.Model):
    """A pricelist the hotel sells through Channex.

    Channex has nothing that is a pricelist: it sells rate plans, one per room
    type and what is included to eat. So this binding holds no Channex id. It
    says that the pricelist is sold there, and the rate plans it becomes are
    ``channel.channex.rate.plan``.
    """

    _name = "channel.channex.product.pricelist"
    _inherit = "channel.channex.binding"
    _inherits = {"product.pricelist": "odoo_id"}
    _description = "Channel Channex Pricelist"

    odoo_id = fields.Many2one(
        comodel_name="product.pricelist",
        string="Pricelist",
        required=True,
        ondelete="cascade",
    )

    @api.model_create_multi
    def create(self, vals_list):
        bindings = super().create(vals_list)
        if not self.env.context.get("connector_no_export"):
            bindings.backend_id._channex_schedule_rate_plans()
        return bindings

    def unlink(self):
        """No longer selling the pricelist through Channex takes its rate plans
        down there."""
        backends = self.backend_id
        result = super().unlink()
        if not self.env.context.get("connector_no_export"):
            backends._channex_schedule_rate_plans()
        return result
