# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo.addons.component.core import Component
from odoo.addons.component_event.components.event import skip_if

from ..product_pricelist_item.listener import buffer_room_type_prices_export

# Fields whose change on the room type warrants a re-export to Wubook.
# These are the ones consumed by the room type export mapper:
# ``name``, ``list_price`` -> ``price``, ``default_code`` -> ``shortname``,
# ``min_price``, ``max_price``, ``default_availability`` -> ``availability``,
# ``class_id`` -> ``rtype``, ``board_service_room_type_ids`` -> ``boards``.
# Also ``room_ids.capacity`` (drives the computed ``occupancy``) is
# relevant, but since ``occupancy`` is stored-and-computed on the binding
# it surfaces here as a write on the binding model rather than on
# ``pms.room.type`` itself; the binding's own listener (inherited from
# base.connector) would handle that branch.
_ROOM_TYPE_RELEVANT_FIELDS = {
    "name",
    "default_code",
    "list_price",
    "min_price",
    "max_price",
    "default_availability",
    "class_id",
    "board_service_room_type_ids",
}

# Fields of the derivation of the price of the room type from another one.
# A change moves the prices of the room type and of the room types deriving
# their price from it, at any level, on the days they derive it, which no
# item change reports: their prices are re-exported to the pricelists of the
# backends they are bound to.
_ROOM_TYPE_PRICE_DERIVATION_FIELDS = {
    "price_base_room_type_id",
    "price_derivation_type",
    "price_derivation_value",
    "price_derivation_rounding",
    "price_derivation_pricelist_ids",
}


class ChannelWubookPmsRoomTypeListener(Component):
    """Auto-push for room types already bound to one or more Wubook
    backends. Enqueues an ``export_record`` job per binding so the actual
    push is asynchronous (the synchronous variant of
    ``channel.wubook.listener`` is intentionally not used here because it
    would block the user's transaction on every save).

    Filters by relevant fields so an unrelated write (e.g. a Many2many
    update on a side field) does not generate noise on Wubook. Bindings
    without ``external_id`` are skipped.

    A change of the price derivation stages instead a re-export of the
    prices of the room type and of the ones deriving their price from it
    (see ``buffer_room_type_prices_export``).
    """

    _name = "channel.wubook.pms.room.type.listener"
    _inherit = "base.connector.listener"
    _apply_on = "pms.room.type"

    @skip_if(lambda self, record, **kwargs: self.no_connector_export(record))
    def on_record_write(self, record, fields=None):
        if fields and set(fields) & _ROOM_TYPE_PRICE_DERIVATION_FIELDS:
            self._stage_price_derivation_export(record)
        if not fields or not (set(fields) & _ROOM_TYPE_RELEVANT_FIELDS):
            return
        for binding in record.channel_wubook_bind_ids:
            if not binding.external_id:
                continue
            binding.with_delay().export_record(binding.backend_id, record)

    def _stage_price_derivation_export(self, record):
        """Stage a re-export of the prices of the room type and of the room
        types deriving their price from it on the pricelist bindings of the
        backends any of them is bound to, on the default window."""
        # The derivation is configuration: follow it whatever the access of
        # the user changing it
        room_types = record.sudo()
        room_types |= room_types._get_price_derivation_descendants()
        room_type_bindings = self.env["channel.wubook.pms.room.type"].search(
            [
                ("odoo_id", "in", room_types.ids),
                ("no_export", "=", False),
                ("external_id", "!=", False),
            ]
        )
        if not room_type_bindings:
            return
        pricelist_bindings = self.env["channel.wubook.product.pricelist"].search(
            [
                ("backend_id", "in", room_type_bindings.backend_id.ids),
                ("external_id", "!=", False),
            ]
        )
        for pricelist_binding in pricelist_bindings:
            buffer_room_type_prices_export(self.env, pricelist_binding, room_types)
