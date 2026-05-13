# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo.addons.component.core import Component
from odoo.addons.component_event.components.event import skip_if

# Fields whose change on the pricelist record itself warrants a re-export
# to Wubook. Item-level changes are handled by the dedicated item listener
# (with transactional coalescing); we don't want a wizard that writes
# ``pricelist.write({"item_ids": [...]})`` to also trigger an
# ``update_plan_name`` here.
_PRICELIST_RELEVANT_FIELDS = {"name"}


class ChannelWubookProductPricelistListener(Component):
    """Auto-push for already-bound pricelists. Items have their own
    dedicated listener with transactional coalescing (see
    ``product_pricelist_item/listener.py``); this listener fires only when
    a relevant pricelist-level field (``name``) is written.

    Always async: the export goes through ``with_delay()``. Bindings without
    ``external_id`` are skipped.
    """

    _name = "channel.wubook.product.pricelist.listener"
    _inherit = "base.connector.listener"
    _apply_on = "product.pricelist"

    @skip_if(lambda self, record, **kwargs: self.no_connector_export(record))
    def on_record_write(self, record, fields=None):
        if not fields or not (set(fields) & _PRICELIST_RELEVANT_FIELDS):
            return
        for binding in record.channel_wubook_bind_ids:
            if not binding.external_id:
                continue
            binding.with_delay().export_record(binding.backend_id, record)
