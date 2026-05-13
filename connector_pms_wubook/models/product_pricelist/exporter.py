# Copyright 2021 Eric Antones <eantones@nuobit.com>
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).


from odoo.addons.component.core import Component


class ChannelWubookProductPricelistDelayedBatchExporter(Component):
    _name = "channel.wubook.product.pricelist.delayed.batch.exporter"
    _inherit = "channel.wubook.delayed.batch.exporter"

    _apply_on = "channel.wubook.product.pricelist"


class ChannelWubookProductPricelistDirectBatchExporter(Component):
    _name = "channel.wubook.product.pricelist.direct.batch.exporter"
    _inherit = "channel.wubook.direct.batch.exporter"

    _apply_on = "channel.wubook.product.pricelist"


class ChannelWubookProductPricelistExporter(Component):
    _name = "channel.wubook.product.pricelist.exporter"
    _inherit = "channel.wubook.exporter"

    _apply_on = "channel.wubook.product.pricelist"

    def _export_dependencies(self):
        """Re-export already-connected dependencies (so they're up to date)
        before the pricelist is exported.

        Strict policy:
        * Only iterate dependencies that ALREADY have a binding on this
          backend. Unconnected room types / parent pricelists are ignored
          — the user must connect them explicitly via the ``Connect to
          Wubook`` wizard. The connector will not auto-create them
          silently from a cascade.
        * Items applicable to other properties (``pms_property_ids`` set
          and not including the backend property) are filtered out, so
          we never mix masters across backends.
        """
        binding = self.binding
        backend = binding.backend_id
        prop = backend.pms_property_id

        applicable_items = binding.item_ids.filtered(
            lambda x: not x.pms_property_ids or prop in x.pms_property_ids
        )

        # Standard items → room types already bound to this backend
        ref_room_types = applicable_items.filtered(
            lambda x: x.wubook_item_type == "standard"
        ).mapped("product_id.room_type_id")
        for room_type in self._filter_bound(
            ref_room_types, "channel.wubook.pms.room.type", backend
        ):
            self._export_dependency(room_type, "channel.wubook.pms.room.type")

        # Virtual items → parent pricelists already bound to this backend
        ref_parents = applicable_items.filtered(
            lambda x: x.wubook_item_type == "virtual"
        ).mapped("base_pricelist_id")
        for parent in self._filter_bound(
            ref_parents, "channel.wubook.product.pricelist", backend
        ):
            self._export_dependency(parent, "channel.wubook.product.pricelist")

        # Flatten pricelists synthesize items at mapping time. We still
        # restrict to room types already bound on this backend.
        if binding.odoo_id.wubook_flatten_to_daily:
            for room_type in binding._get_flatten_export_room_types():
                self._export_dependency(room_type, "channel.wubook.pms.room.type")

    def _filter_bound(self, records, binding_model, backend):
        """Return only records that already have a binding on ``backend``
        in the given ``binding_model``. Used to keep cascades strict.
        """
        if not records:
            return records
        bound_ids = (
            self.env[binding_model]
            .search(
                [
                    ("backend_id", "=", backend.id),
                    ("odoo_id", "in", records.ids),
                ]
            )
            .mapped("odoo_id.id")
        )
        return records.filtered(lambda r: r.id in bound_ids)

    def _has_to_skip(self):
        return any(
            [
                self.binding.synced_export,
            ]
        )

    def _after_export(self):
        # Snapshot the name we just pushed so subsequent exports triggered
        # by item changes can skip the redundant ``update_plan_name``.
        super()._after_export()
        if self.binding:
            current_name = self.binding.name
            if self.binding.wubook_last_synced_name != current_name:
                self.binding.with_context(
                    connector_no_export=True
                ).write({"wubook_last_synced_name": current_name})
