# Copyright 2021 Eric Antones <eantones@nuobit.com>
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
import logging

from odoo import _
from odoo.exceptions import ValidationError

from odoo.addons.component.core import Component
from odoo.addons.connector.components.mapper import mapping, only_create

_logger = logging.getLogger(__name__)


class ChannelWubookProductPricelistMapperImport(Component):
    _name = "channel.wubook.product.pricelist.mapper.import"
    _inherit = "channel.wubook.mapper.import"

    _apply_on = "channel.wubook.product.pricelist"

    children = [
        ("items", "channel_wubook_item_ids", "channel.wubook.product.pricelist.item")
    ]

    @only_create
    @mapping
    def backend_id(self, record):
        return {"backend_id": self.backend_record.id}

    @only_create
    @mapping
    def name(self, record):
        return {"name": record["name"]}

    # @only_create
    @mapping
    def pricelist_type(self, record):
        if record["type"] == "standard":
            if record["daily"] != 1:
                raise ValidationError(_("Only 'Daily' pricelists are supported"))
            return {"pricelist_type": "daily"}
        elif record["type"] == "virtual":
            return {"pricelist_type": False}
        else:
            raise ValidationError(_("Price plan type '%s' unexpected") % record["type"])

    @mapping
    def property_ids(self, record):
        binding = self.options.get("binding")
        has_pms_properties = binding and bool(binding.pms_property_ids)
        if self.options.for_create or has_pms_properties:
            return {
                "pms_property_ids": [(4, self.backend_record.pms_property_id.id, 0)]
            }


class ChannelWubookProductPricelistChildBinderMapperImport(Component):
    _name = "channel.wubook.product.pricelist.child.binder.mapper.import"
    _inherit = "channel.wubook.child.binder.mapper.import"
    _apply_on = "channel.wubook.product.pricelist.item"

    def get_all_items(self, mapper, items, parent, to_attr, options):
        items_values = super().get_all_items(mapper, items, parent, to_attr, options)
        if (
            parent.source["type"] == "standard"
            and self.env["pms.room.type"]._has_price_derivation()
        ):
            items_values = self._skip_price_derived_items(items_values, parent, options)
        return items_values

    def _skip_price_derived_items(self, items_values, parent, options):
        """Leave out the new items of the room types deriving their price in
        the pricelist (see ``pms.room.type.price_base_room_type_id``).

        Wubook has a price for every room and day, the derived ones included,
        but a new item of their own would be an override: the room type would
        stop following the price of its base room type on its dates. The
        items they already have, overrides, are updated as any other one
        (see ``get_item_values``).
        """
        binding = options.get("binding")
        pricelist = binding.odoo_id if binding else self.env["product.pricelist"]
        RoomType = self.env["pms.room.type"].with_context(active_test=False)
        derived_by_product = {}
        kept_values = []
        for values in items_values:
            product_id = values.get("product_id")
            if "id" in values or "odoo_id" in values or not product_id:
                kept_values.append(values)
                continue
            if product_id not in derived_by_product:
                room_type = RoomType.search([("product_id", "=", product_id)], limit=1)
                derived_by_product[product_id] = bool(
                    room_type and self._is_price_derived(room_type, pricelist)
                )
            if not derived_by_product[product_id]:
                kept_values.append(values)
        skipped = len(items_values) - len(kept_values)
        if skipped:
            _logger.info(
                "Skipped %d new prices of the Wubook plan %s for room types "
                "deriving their price in it",
                skipped,
                parent.source.get("name") or parent.source.get("id"),
            )
        return kept_values

    def _is_price_derived(self, room_type, pricelist):
        """Whether the room type derives its price in the pricelist, which
        may be the one being created by the import."""
        if pricelist:
            return bool(room_type._get_price_derivation(pricelist))
        # A new pricelist: only the room types deriving their price in all
        # the pricelists derive it there
        room_type = room_type.sudo()
        return bool(
            room_type.price_base_room_type_id
            and not room_type.price_derivation_pricelist_ids
        )

    def get_item_values(self, map_record, to_attr, options):
        values = super().get_item_values(map_record, to_attr, options)
        binding = options.get("binding")
        if binding:
            plan_type = map_record.parent.source["type"]
            if plan_type == "virtual":
                item_ids = binding.item_ids.filtered(
                    lambda x: all(
                        [
                            x.applied_on == values["applied_on"],
                            x.compute_price == values["compute_price"],
                            x.base == values["base"],
                            x.base_pricelist_id.id == values["base_pricelist_id"],
                            x.pms_property_ids == self.backend_record.pms_property_id,
                        ]
                    )
                )
            elif plan_type == "standard":
                item_ids = binding.item_ids.filtered(
                    lambda x: all(
                        [
                            x.applied_on == values["applied_on"],
                            x.compute_price == values["compute_price"],
                            x.product_id.id == values["product_id"],
                            x.date_start_consumption
                            == values["date_start_consumption"],
                            x.date_end_consumption == values["date_end_consumption"],
                            x.pms_property_ids == self.backend_record.pms_property_id,
                        ]
                    )
                )
            else:
                raise ValidationError(_("Unexpected pricelist type '%s'") % plan_type)

            if item_ids:
                if len(item_ids) > 1:
                    raise ValidationError(
                        _(
                            "Found two pricelist items with same data %s. "
                            "Please remove one of them"
                        )
                        % values
                    )
                item_binding = self.binder_for().wrap_record(item_ids)
                if item_binding:
                    values["id"] = item_binding.id
                else:
                    values["odoo_id"] = item_ids.id

        return values

    def format_items(self, items_values):
        items_values = sorted(
            items_values,
            key=lambda x: x["applied_on"] == "0_product_variant"
            and (x["product_id"], x["date_start_consumption"])
            or (),
            reverse=True,
        )
        ops = []
        for values in items_values:
            _id = values.pop("id", None)
            if _id:
                ops.append((1, _id, values))
            else:
                ops.append((0, 0, values))

        return ops
