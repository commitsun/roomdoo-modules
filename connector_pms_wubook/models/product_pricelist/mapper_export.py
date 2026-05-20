# Copyright 2021 Eric Antones <eantones@nuobit.com>
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import _
from odoo.exceptions import ValidationError

from odoo.addons.component.core import Component
from odoo.addons.connector.components.mapper import mapping, only_create


class ChannelWubookProductPricelistMapperExport(Component):
    _name = "channel.wubook.product.pricelist.mapper.export"
    _inherit = "channel.wubook.mapper.export"

    _apply_on = "channel.wubook.product.pricelist"

    children = [
        ("channel_wubook_item_ids", "items", "channel.wubook.product.pricelist.item")
    ]

    @mapping
    def name(self, record):
        """Only emit ``name`` when it differs from the last value pushed to
        Wubook (or has never been pushed). The adapter calls
        ``update_plan_name`` only when ``name`` is present in the payload,
        so this skip avoids redundant XMLRPC traffic when the export was
        triggered by item changes via the scheduler.
        """
        last = record.wubook_last_synced_name
        if last and last == record.name:
            return None
        return {"name": record.name}

    @only_create
    @mapping
    def pricelist_type(self, record):
        if record.pricelist_type != "daily" and not record.wubook_flatten_to_daily:
            raise ValidationError(_("Only 'Daily' pricelists are supported"))
        return {"daily": 1}

    @mapping
    def pricelist_plan_type(self, record):
        return {"type": record.wubook_plan_type}

    @mapping
    def items_flatten(self, record):
        """For pricelists marked as ``wubook_flatten_to_daily``, replace the
        normal items mapping by a synthetic list computed on the fly over
        the configured default window. The child binder mapper is
        short-circuited to an empty recordset, so this value wins.
        """
        if not record.wubook_flatten_to_daily:
            return None
        date_from, date_to = record._get_flatten_default_window()
        if not date_from or not date_to:
            return {"items": []}
        items = record._compute_flatten_payload_items(date_from, date_to)
        return {"items": items}


class ChannelWubookProductPricelistChildBinderMapperExport(Component):
    _name = "channel.wubook.product.pricelist.child.binder.mapper.export"
    _inherit = "channel.wubook.child.binder.mapper.export"

    _apply_on = "channel.wubook.product.pricelist.item"

    def skip_item(self, map_record):
        if (
            map_record.source.wubook_item_type == "standard"
            and map_record.source.date_start_consumption
            != map_record.source.date_end_consumption
        ):
            raise ValidationError(
                _("Consumption dates must be the same on daily standard pricelists")
            )
        # flake8: noqa: B950
        return any(
            [
                map_record.source.wubook_item_type == "standard"
                and map_record.source.product_id.room_type_id.class_id.default_code
                in self.backend_record.backend_type_id.child_id.room_type_class_ids.get_nosync_shortnames(),  # noqa: E501
                not map_record.source.wubook_item_type
                or map_record.parent.source.wubook_plan_type
                != map_record.source.wubook_item_type,
                map_record.source.pms_property_ids
                and self.backend_record.pms_property_id
                not in map_record.source.pms_property_ids,
                map_record.source.synced_export,
                map_record.source.wubook_item_type == "standard"
                and not map_record.source.odoo_id.wubook_date_valid(),
                map_record.source.wubook_item_type == "standard"
                and not map_record.source.product_id.room_type_id.channel_wubook_bind_ids.filtered(  # noqa: E501
                    lambda x: x.backend_id == self.backend_record
                ),
            ]
        )

    def get_all_items(self, mapper, items, parent, to_attr, options):
        # Flatten pricelists provide their items via the parent mapping
        # ``items_flatten``; short-circuit the normal child binder flow.
        if parent.source.wubook_flatten_to_daily:
            return []
        # Resolve "items of this pricelist that apply to this backend's
        # property and still lack a binding for this backend" with a
        # single SQL query. The naive ``parent.source["item_ids"].filtered(...)``
        # walks every item of the pricelist in Python (~317k for the
        # global "Tarifa Estándar"), turning every export into a multi-
        # minute job even when no new bindings need to be created.
        # ``pms_property_ids`` empty means the item applies globally;
        # otherwise the backend's property must be included.
        backend = self.backend_record
        bindings = items.filtered(lambda x: x.backend_id == backend)
        self.env.cr.execute(
            """
            SELECT i.id
            FROM product_pricelist_item i
            LEFT JOIN channel_wubook_product_pricelist_item ib
                ON ib.odoo_id = i.id AND ib.backend_id = %s
            WHERE i.pricelist_id = %s
              AND ib.id IS NULL
              AND (
                NOT EXISTS (
                    SELECT 1 FROM product_pricelist_item_pms_property_rel rel
                    WHERE rel.product_pricelist_item_id = i.id
                )
                OR EXISTS (
                    SELECT 1 FROM product_pricelist_item_pms_property_rel rel
                    WHERE rel.product_pricelist_item_id = i.id
                      AND rel.pms_property_id = %s
                )
              )
            """,
            (backend.id, parent.source.odoo_id.id, backend.pms_property_id.id),
        )
        new_item_ids = [row[0] for row in self.env.cr.fetchall()]
        if new_item_ids:
            new_items = self.env["product.pricelist.item"].browse(new_item_ids)
            new_binding_ids = [
                self.binder_for().wrap_record(i, force=True).id for i in new_items
            ]
            items = items.browse(new_binding_ids) | bindings
        else:
            items = bindings
        return super().get_all_items(mapper, items, parent, to_attr, options)
