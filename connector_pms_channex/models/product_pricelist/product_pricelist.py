# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import fields, models

BINDING_MODEL = "channel.channex.product.pricelist"


class ProductPricelist(models.Model):
    _inherit = "product.pricelist"

    channel_channex_bind_ids = fields.One2many(
        comodel_name=BINDING_MODEL,
        inverse_name="odoo_id",
        string="Channel Channex Bindings",
    )
    channex_rate_plan_ids = fields.One2many(
        comodel_name="channel.channex.rate.plan",
        inverse_name="pricelist_id",
        string="Rate plans on Channex",
    )

    def _channex_backends(self):
        """The backends with rate plans of these pricelists, or that should
        have them.

        Archived ones included: a binding delegates ``active`` to its
        pricelist, so archiving the pricelist hides its bindings, and that is
        precisely when its rate plans have to be taken down.
        """
        bindings = (
            self.env[BINDING_MODEL]
            .with_context(active_test=False)
            .search([("odoo_id", "in", self.ids)])
        )
        # Rebuilt without that context, which would otherwise travel with the
        # backends into the job and have it count the archived ones as sold.
        return self.env["channel.channex.backend"].browse(
            (bindings.backend_id | self.channex_rate_plan_ids.backend_id).ids
        )
