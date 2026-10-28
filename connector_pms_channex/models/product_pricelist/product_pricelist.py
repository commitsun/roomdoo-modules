# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import fields, models

from ..common.ari import stage_ari

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

    def _channex_dependent_pricelists(self):
        """These pricelists and every one priced from them, however far down:
        a price of a pricelist is a price of the ones based on it too."""
        pricelists = todo = self
        while todo:
            todo = (
                self.env["product.pricelist.item"]
                .search(
                    [("base", "=", "pricelist"), ("base_pricelist_id", "in", todo.ids)]
                )
                .pricelist_id
                - pricelists
            )
            pricelists |= todo
        return pricelists

    def _channex_stage_restrictions(
        self,
        room_type=None,
        board_service=None,
        date_from=None,
        date_to=None,
        pms_properties=None,
    ):
        """Publish again the rates and restrictions of these pricelists' rate
        plans: of one room type or board service only, if given, between those
        nights, and in those properties."""
        for pricelist in self:
            for pms_property_id in pms_properties.ids if pms_properties else [None]:
                stage_ari(
                    self.env,
                    "restrictions",
                    (
                        pms_property_id,
                        pricelist.id,
                        room_type.id if room_type else None,
                        board_service.id if board_service else None,
                    ),
                    date_from,
                    date_to,
                )
