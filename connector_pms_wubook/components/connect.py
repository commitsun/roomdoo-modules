# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo.addons.component.core import Component


class ChannelWubookConnectHooks(Component):
    """Re-export what was skipped while a room type was not connected yet.

    Pricelist items and availability plan rules of an unbound room type are
    skipped on export. Once the room type is connected, every pricelist or plan
    already bound on this backend that references it has to be pushed again.
    """

    _name = "channel.wubook.connect.hooks"
    _inherit = "channel.connect.hooks"
    _collection = "channel.wubook.backend"

    def after_connect(self, record):
        if record._name != "pms.room.type":
            return None
        backend = self.backend_record

        # product.room_type_id is computed and not stored, so it cannot be used
        # in a search domain: resolve the products first and filter pricelists
        # by the products their items reference.
        product_ids = record.product_id.ids
        if product_ids:
            pricelist_bindings = self.env["channel.wubook.product.pricelist"].search(
                [
                    ("backend_id", "=", backend.id),
                    ("external_id", "!=", 0),
                    ("odoo_id.item_ids.product_id", "in", product_ids),
                ]
            )
            for binding in pricelist_bindings:
                binding.with_delay().export_record(backend, binding.odoo_id)

        plan_bindings = self.env["channel.wubook.pms.availability.plan"].search(
            [
                ("backend_id", "=", backend.id),
                ("external_id", "!=", 0),
                ("odoo_id.rule_ids.room_type_id", "=", record.id),
            ]
        )
        for binding in plan_bindings:
            binding.with_delay().export_record(backend, binding.odoo_id)
        return None
