# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import models

#: Fields of an item that move a price, or the nights and products it sets.
PRICE_FIELDS = {
    "fixed_price",
    "compute_price",
    "percent_price",
    "price_discount",
    "price_surcharge",
    "price_round",
    "price_min_margin",
    "price_max_margin",
    "base",
    "base_pricelist_id",
    "applied_on",
    "product_id",
    "product_tmpl_id",
    "categ_id",
    "min_quantity",
    "date_start_consumption",
    "date_end_consumption",
    "date_start",
    "date_end",
    "pms_property_ids",
    "pricelist_id",
    "active",
}


class ProductPricelistItem(models.Model):
    _inherit = "product.pricelist.item"

    def _channex_stage_restrictions(self):
        """Publish again the prices these items set, on their pricelists and
        on the ones priced from them.

        An item of a room type moves its rate plans; one of a board service,
        the rate plans that sell it; any other, every rate plan, as it can
        reach both through a category or the pricelist as a whole.
        """
        for item in self:
            room_types = board_service = None
            template = item.product_tmpl_id or item.product_id.product_tmpl_id
            if item.applied_on in ("0_product_variant", "1_product"):
                if template.is_board_service:
                    board_service = template
                else:
                    room_types = self.env["pms.room.type"].search(
                        [("product_id", "=", item.product_id.id)]
                        if item.applied_on == "0_product_variant"
                        else [("product_id.product_tmpl_id", "=", template.id)]
                    )
                    if not room_types:
                        # Neither a room nor a board service.
                        continue
            pricelists = item.pricelist_id._channex_dependent_pricelists()
            for room_type in room_types or [None]:
                pricelists._channex_stage_restrictions(
                    room_type=room_type,
                    board_service=board_service,
                    date_from=item.date_start_consumption,
                    date_to=item.date_end_consumption,
                    pms_properties=item.pms_property_ids,
                )

    def write(self, vals):
        if PRICE_FIELDS & set(vals) and not self.env.context.get("connector_no_export"):
            # An item moved to other nights, products or pricelists stops
            # pricing the ones it leaves, which the listener cannot see any
            # more: they are read here, before they go.
            self._channex_stage_restrictions()
        return super().write(vals)
