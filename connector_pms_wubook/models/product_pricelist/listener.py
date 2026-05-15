# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo.addons.component.core import Component
from odoo.addons.component_event.components.event import skip_if

# Fields whose change on the pricelist record itself warrants a re-export
# to Wubook. Item-level changes are handled by the dedicated item listener
# (with transactional coalescing); we don't want a wizard that writes
# ``pricelist.write({"item_ids": [...]})`` to also trigger an
# ``update_plan_name`` here.
#
# * ``name``: pushed via ``update_plan_name`` / ``rplan_rename_rplan`` in
#   the mapper.
# * ``wubook_flatten_to_daily``: toggles the export TYPE (a regular
#   pricelist with derived items vs. a fully-materialised daily plan).
#   Without this, flipping the flag would leave the Wubook side stale
#   until the next item change.
#
# Pricelist hierarchy is modelled via ``item.base_pricelist_id`` (not via
# a header-level ``parent_id``), so re-parenting flows already go through
# the item listener.
_PRICELIST_RELEVANT_FIELDS = {"name", "wubook_flatten_to_daily"}

# Fields whose change re-evaluates the pricing chain and so require a
# re-push of the actual price values (not just metadata like ``name``).
_PRICELIST_PRICING_FIELDS = {"wubook_flatten_to_daily"}


class ChannelWubookProductPricelistListener(Component):
    """Auto-push for already-bound pricelists. Items have their own
    dedicated listener with transactional coalescing (see
    ``product_pricelist_item/listener.py``); this listener fires only when
    a relevant pricelist-level field is written.

    Dispatch rule:

    * If the change touches ``parent_id`` / ``wubook_flatten_to_daily``
      AND the pricelist is currently ``wubook_flatten_to_daily=True``,
      the prices themselves need re-evaluation → enqueue
      ``export_flattened()`` (full default window).
    * Otherwise (plain rename, or a regular pricelist whose items the
      item listener already handles) → enqueue ``export_record`` with a
      coarse ``identity_key`` so concurrent writes collapse to a single
      PENDING job per binding.

    Always async. Bindings without ``external_id`` are skipped.
    """

    _name = "channel.wubook.product.pricelist.listener"
    _inherit = "base.connector.listener"
    _apply_on = "product.pricelist"

    @skip_if(lambda self, record, **kwargs: self.no_connector_export(record))
    def on_record_write(self, record, fields=None):
        if not fields or not (set(fields) & _PRICELIST_RELEVANT_FIELDS):
            return
        needs_flatten_prices = (
            bool(set(fields) & _PRICELIST_PRICING_FIELDS)
            and record.wubook_flatten_to_daily
        )
        for binding in record.channel_wubook_bind_ids:
            if not binding.external_id:
                continue
            if needs_flatten_prices:
                binding.with_delay().export_flattened()
            else:
                binding.with_delay(
                    identity_key="wubook_export_record:%s:%s"
                    % (binding._name, binding.id)
                ).export_record(binding.backend_id, record)
