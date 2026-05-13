# Copyright 2021 Eric Antones <eantones@nuobit.com>
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class ProductPricelist(models.Model):
    _name = "product.pricelist"
    _inherit = ["product.pricelist", "channel.wubook.connect.mixin"]

    channel_wubook_bind_ids = fields.One2many(
        comodel_name="channel.wubook.product.pricelist",
        inverse_name="odoo_id",
        string="Channel Wubook PMS Bindings",
    )

    wubook_flatten_to_daily = fields.Boolean(
        string="Flatten to Daily on Wubook",
        default=False,
        help=(
            "If set, this pricelist will be exported to Wubook as an independent "
            "daily plan, computing the final price for each room type and date on "
            "the fly by walking the full chain of parent pricelists. Use this when "
            "you need more than one level of derived pricelists (e.g. A -> B +15% "
            "-> C +breakfast), which Wubook does not support natively."
        ),
    )

    wubook_plan_type = fields.Selection(
        selection=[("virtual", "Virtual"), ("standard", "Standard")],
        readonly=True,
        store=True,
        compute="_compute_wubook_plan_type",
    )

    @api.depends("item_ids.wubook_item_type", "wubook_flatten_to_daily")
    def _compute_wubook_plan_type(self):
        for rec in self:
            if rec.wubook_flatten_to_daily:
                rec.wubook_plan_type = "standard"
                continue
            if rec.pricelist_type == "daily":
                item_types = rec.item_ids.mapped("wubook_item_type")
                if "standard" in item_types:
                    rec.wubook_plan_type = "standard"
                else:
                    rec.wubook_plan_type = False
            else:
                virtual_items = rec.item_ids.filtered(
                    lambda x: x.wubook_item_type == "virtual"
                )
                if len(virtual_items) == 1:
                    rec.wubook_plan_type = "virtual"
                else:
                    rec.wubook_plan_type = False

    @api.constrains("wubook_flatten_to_daily", "item_ids")
    def _check_wubook_flatten_to_daily(self):
        for rec in self:
            if not rec.wubook_flatten_to_daily:
                continue
            derived_items = rec.item_ids.filtered(
                lambda x: x.compute_price in ("formula", "percentage")
                and x.base == "pricelist"
                and x.base_pricelist_id
            )
            if not derived_items:
                raise ValidationError(
                    _(
                        "Pricelist '%s' is marked to be flattened to daily for "
                        "Wubook but has no items deriving from another pricelist. "
                        "Add at least one rule with 'Based on' = 'Other Pricelist' "
                        "before enabling this option."
                    )
                    % rec.display_name
                )

    def _get_flatten_parent_pricelists(self):
        """Return the set of parent pricelists this pricelist derives from
        (transitively), walking ``base_pricelist_id`` on items whose base is
        another pricelist. Cycle-safe.
        """
        self.ensure_one()
        Pricelist = self.env["product.pricelist"]
        visited = set()
        result = Pricelist.browse()
        stack = [self]
        while stack:
            current = stack.pop()
            if current.id in visited:
                continue
            visited.add(current.id)
            parents = current.item_ids.filtered(
                lambda x: x.base == "pricelist" and x.base_pricelist_id
            ).mapped("base_pricelist_id")
            for parent in parents:
                if parent.id in visited:
                    continue
                result |= parent
                stack.append(parent)
        return result

    def _get_flatten_descendant_pricelists(self):
        """Return descendants of ``self`` that are marked ``wubook_flatten_to_daily``
        and depend (transitively) on ``self`` through ``base_pricelist_id``.
        Cycle-safe.
        """
        self.ensure_one()
        Pricelist = self.env["product.pricelist"]
        visited = set()
        result = Pricelist.browse()
        stack = [self]
        while stack:
            current = stack.pop()
            if current.id in visited:
                continue
            visited.add(current.id)
            children_items = self.env["product.pricelist.item"].search(
                [
                    ("base", "=", "pricelist"),
                    ("base_pricelist_id", "=", current.id),
                ]
            )
            children = children_items.mapped("pricelist_id")
            for child in children:
                if child.id in visited:
                    continue
                if child.wubook_flatten_to_daily:
                    result |= child
                stack.append(child)
        return result

    def _get_flatten_chain_max_date(self):
        """Return the latest ``date_end_consumption`` found across the whole
        chain of parent pricelists. ``False`` if no parent has any dated rule
        (in which case the caller should fall back to a default window).
        """
        self.ensure_one()
        parents = self._get_flatten_parent_pricelists()
        if not parents:
            return False
        dates = parents.mapped("item_ids.date_end_consumption")
        dates = [d for d in dates if d]
        return max(dates) if dates else False

    def _compute_flattened_items(
        self, pms_property, room_types, date_from, date_to
    ):
        """Compute synthetic 'daily standard' items for this pricelist by
        evaluating the full pricelist chain (parents included) for each
        (room_type, date) pair in the given window.

        Returns a list of dicts shaped like a ``product.pricelist.item``:
            {
                "product_id": <product.product>,
                "room_type_id": <pms.room.type>,
                "date": <date>,
                "fixed_price": <float>,
                "currency_id": <res.currency>,
            }

        Days are inclusive on both ends. The caller is responsible for any
        further capping (e.g. against ``_get_flatten_chain_max_date()``).
        """
        self.ensure_one()
        if not room_types or not date_from or not date_to or date_to < date_from:
            return []
        pricelist = self.with_context(
            pms_property_id=pms_property.id,
            allowed_pms_property_ids=pms_property.ids,
        )
        results = []
        current = date_from
        while current <= date_to:
            for room_type in room_types:
                product = room_type.product_id
                if not product:
                    continue
                price_map = pricelist._compute_price_rule(
                    product,
                    1,
                    date=fields.Datetime.now(),
                    consumption_date=current,
                    pms_property_id=pms_property.id,
                )
                price = price_map.get(product.id, (0.0, False))[0]
                results.append(
                    {
                        "product_id": product,
                        "room_type_id": room_type,
                        "date": current,
                        "fixed_price": price,
                        "currency_id": pricelist.currency_id,
                    }
                )
            current += timedelta(days=1)
        return results
