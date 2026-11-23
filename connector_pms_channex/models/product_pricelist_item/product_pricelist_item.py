# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import api, fields, models
from odoo.tools import format_date

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

#: Fields of an item that decide whether a price it gives can change with the
#: date: when it applies, to what, and where it takes its price from.
DATED_PRICE_FIELDS = {
    "applied_on",
    "product_id",
    "product_tmpl_id",
    "categ_id",
    "min_quantity",
    "date_start_consumption",
    "date_end_consumption",
    "date_start",
    "date_end",
    "compute_price",
    "base",
    "base_pricelist_id",
    "pms_property_ids",
    "pricelist_id",
}

#: The dates an item can be limited to, of the nights and of the sale.
DATE_FIELDS = (
    "date_start_consumption",
    "date_end_consumption",
    "date_start",
    "date_end",
)


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
                    # An end left open is every night on that side.
                    date_from=item.date_start_consumption or None,
                    date_to=item.date_end_consumption or None,
                    pms_properties=item.pms_property_ids,
                )

    def _channex_prices_board(self):
        """Whether this item can price a board service: it is of one, or of a
        whole category or pricelist."""
        self.ensure_one()
        if self.applied_on in ("2_product_category", "3_global"):
            return True
        return (
            self.product_tmpl_id or self.product_id.product_tmpl_id
        ).is_board_service

    def _channex_board_pricelists(self):
        """The pricelists whose board services these items can price: theirs,
        and the ones priced from them."""
        return self.filtered(
            lambda item: item._channex_prices_board()
        ).pricelist_id._channex_dependent_pricelists()

    def _channex_schedule_rate_plans(self):
        """Bring in line the rate plans whose children fee these items can
        price, which includes what a child adds to the board service."""
        # As superuser: anyone editing a pricelist can get here.
        pricelists = self.sudo()._channex_board_pricelists()
        pricelists._channex_backends()._channex_schedule_rate_plans()

    def _channex_dates(self):
        """The dates this item is limited to, as they read in its form, or
        nothing when it applies on every date."""
        self.ensure_one()
        return ", ".join(
            f"{self._fields[name]._description_string(self.env)}: "
            f"{format_date(self.env, self[name])}"
            for name in DATE_FIELDS
            if self[name]
        )

    def _channex_is_over(self):
        """Whether this item is over: its last night, or its last day of sale,
        is behind."""
        self.ensure_one()
        return bool(
            self.date_end_consumption
            and self.date_end_consumption < fields.Date.context_today(self)
            or self.date_end
            and self.date_end < fields.Datetime.now()
        )

    @api.constrains(*DATED_PRICE_FIELDS)
    def _check_channex_children_board(self):
        self._channex_board_pricelists()._channex_check_children_board()

    def write(self, vals):
        if PRICE_FIELDS & set(vals) and not self.env.context.get("connector_no_export"):
            # An item moved to other nights, products or pricelists stops
            # pricing the ones it leaves, which the listener cannot see any
            # more: they are read here, before they go.
            self._channex_stage_restrictions()
            self._channex_schedule_rate_plans()
        return super().write(vals)

    def unlink(self):
        """An item gone hands its products to the next one that applies to
        them, which may be one with dates."""
        pricelists = self._channex_board_pricelists()
        result = super().unlink()
        pricelists._channex_check_children_board()
        return result
