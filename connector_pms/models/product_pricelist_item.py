# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
from odoo import models


class ProductPricelistItem(models.Model):
    _inherit = "product.pricelist.item"

    def _apply_occupancy_price(self, product, price, **kwargs):
        """Add the fee of the children staying to the price of the room.

        The price derived from the occupancy is the one for the adults, as the
        occupancy of a room type counts them alone. Children are charged on top
        of it, the same way channel managers do.
        """
        price = super()._apply_occupancy_price(product, price, **kwargs)
        children = kwargs.get("children")
        room_type = product.room_type_id if children else False
        if not room_type:
            return price
        occupancy_rule = room_type._get_occupancy_rule(
            kwargs.get("pricelist") or self.pricelist_id
        )
        if not occupancy_rule.children_fee:
            return price
        return price + occupancy_rule.children_fee * children
