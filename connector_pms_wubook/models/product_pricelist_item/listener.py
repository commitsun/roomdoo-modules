# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo.addons.component.core import Component
from odoo.addons.component_event.components.event import skip_if

# Sentinel key for the flatten-export buffer.
_FLATTEN_BUFFER_KEY = "connector_pms_wubook.flatten_buffer"

# Sentinel key for the regular-pricelist export buffer. A *regular* (non
# flatten) pricelist whose item just changed needs a full ``export_record``
# of the binding so the mapper can push only the dirty items via
# ``update_plan_prices``. We buffer one entry per binding and emit a
# single job per binding at precommit — same coalescence pattern as the
# flatten buffer.
_REGULAR_PRICELIST_BUFFER_KEY = "connector_pms_wubook.regular_pricelist_buffer"

# Sentinel inserted in the ``room_type_ids`` set to mark "all room types"
# (used when the change affects the rule globally, e.g. the flatten
# pricelist's own % rule or a parent rule with no specific room type).
_ALL_ROOM_TYPES = None


def _flush_flatten_buffer(env):
    """Precommit callback: read the per-binding accumulated date ranges
    and room types and enqueue **one** ``export_flattened`` job per binding
    with the merged window and the minimal room-type scope.
    """
    data = env.cr.precommit.data.pop(_FLATTEN_BUFFER_KEY, None)
    if not data:
        return
    for _binding_id, info in data.items():
        binding = info["binding"].exists()
        if not binding:
            continue
        ranges = info["ranges"]
        room_type_ids = info["room_type_ids"]
        # ``_ALL_ROOM_TYPES`` in the set means we cannot scope to specific
        # room types (the change is global). Forward as full window.
        scope_all_room_types = _ALL_ROOM_TYPES in room_type_ids
        # Same logic for dates: a (None, None) entry means "default
        # window".
        if any(r == (None, None) for r in ranges):
            kwargs = {}
            if not scope_all_room_types:
                kwargs["room_type_ids"] = sorted(
                    rid for rid in room_type_ids if rid is not _ALL_ROOM_TYPES
                )
            binding.with_delay().export_flattened(**kwargs)
            continue
        dfroms = [r[0] for r in ranges if r[0]]
        dtos = [r[1] for r in ranges if r[1]]
        if not dfroms or not dtos:
            kwargs = {}
            if not scope_all_room_types:
                kwargs["room_type_ids"] = sorted(
                    rid for rid in room_type_ids if rid is not _ALL_ROOM_TYPES
                )
            binding.with_delay().export_flattened(**kwargs)
            continue
        kwargs = {"date_from": min(dfroms), "date_to": max(dtos)}
        if not scope_all_room_types:
            kwargs["room_type_ids"] = sorted(
                rid for rid in room_type_ids if rid is not _ALL_ROOM_TYPES
            )
        binding.with_delay().export_flattened(**kwargs)


def _flush_regular_pricelist_buffer(env):
    """Precommit callback: enqueue **one** ``export_record`` job per
    regular (non-flatten) pricelist binding accumulated during the
    transaction. The mapper / adapter already filter items by
    ``synced_export`` so only the dirty items reach Wubook.
    """
    data = env.cr.precommit.data.pop(_REGULAR_PRICELIST_BUFFER_KEY, None)
    if not data:
        return
    for _binding_id, binding in data.items():
        binding = binding.exists()
        if not binding:
            continue
        binding.with_delay().export_record(binding.backend_id, binding.odoo_id)


class ChannelWubookProductPricelistItemListener(Component):
    """Cascade listener for every ``product.pricelist.item`` change.

    Within a single transaction, requests are coalesced through two
    independent buffers on ``cr.precommit.data`` and flushed as a single
    job per affected binding:

    * **Regular pricelist binding** (the pricelist owning the item):
      enqueues ``export_record(backend, pricelist)`` so the mapper can
      push the dirty items via ``update_plan_prices``. Replaces the old
      ``_scheduler_export_pricelist_items`` cron — pushes are immediate
      instead of waiting for the next cron tick.

    * **Flatten descendants** of the pricelist: enqueues
      ``export_flattened(date_from, date_to, room_type_ids)`` with the
      minimal date / room-type scope to avoid re-pushing prices that
      didn't change.

    Bindings without ``external_id`` are skipped (the wizard's connect
    flow is still in progress).
    """

    _name = "channel.wubook.product.pricelist.item.listener"
    _inherit = "base.connector.listener"
    _apply_on = "product.pricelist.item"

    def _buffer_flatten_export(self, binding, date_from, date_to, room_type_id):
        cr = self.env.cr
        data = cr.precommit.data
        if _FLATTEN_BUFFER_KEY not in data:
            data[_FLATTEN_BUFFER_KEY] = {}
            env = self.env  # captured for the precommit closure
            cr.precommit.add(lambda env=env: _flush_flatten_buffer(env))
        entry = data[_FLATTEN_BUFFER_KEY].setdefault(
            binding.id,
            {"binding": binding, "ranges": [], "room_type_ids": set()},
        )
        entry["ranges"].append((date_from, date_to))
        entry["room_type_ids"].add(room_type_id)

    def _buffer_regular_export(self, binding):
        cr = self.env.cr
        data = cr.precommit.data
        if _REGULAR_PRICELIST_BUFFER_KEY not in data:
            data[_REGULAR_PRICELIST_BUFFER_KEY] = {}
            env = self.env
            cr.precommit.add(
                lambda env=env: _flush_regular_pricelist_buffer(env)
            )
        data[_REGULAR_PRICELIST_BUFFER_KEY].setdefault(binding.id, binding)

    def _enqueue_pricelist_exports(self, record, date_from=None, date_to=None):
        """Buffer the appropriate export for every Wubook binding affected
        by a change on ``record``.

        Logic:

        * Pricelist's own bindings:
          - If ``wubook_flatten_to_daily`` → buffer flatten export with
            ``(None, None)`` (default window) and ``_ALL_ROOM_TYPES``
            scope: the percentage rule on the flatten pricelist applies
            globally so we can't scope per room type.
          - Else → buffer a regular ``export_record``.
        * Flatten descendants of the pricelist: buffer flatten export
          scoped to the changed item's date range and to the room type
          tied to ``product_id`` (or "all" if undeterminable).
        """
        pricelist = record.pricelist_id
        if not pricelist:
            return

        # Own bindings
        if pricelist.wubook_flatten_to_daily:
            for binding in pricelist.channel_wubook_bind_ids:
                if not binding.external_id:
                    continue
                self._buffer_flatten_export(
                    binding, None, None, _ALL_ROOM_TYPES
                )
        else:
            for binding in pricelist.channel_wubook_bind_ids:
                if not binding.external_id:
                    continue
                self._buffer_regular_export(binding)

        # Cascade to flatten descendants
        descendants = pricelist._get_flatten_descendant_pricelists()
        if not descendants:
            return
        item_dfrom = record.date_start_consumption if not date_from else date_from
        item_dto = record.date_end_consumption if not date_to else date_to
        room_type = (
            record.product_id.room_type_id if record.product_id else False
        )
        affected_room_type_id = room_type.id if room_type else _ALL_ROOM_TYPES
        for descendant in descendants:
            for binding in descendant.channel_wubook_bind_ids:
                if not binding.external_id:
                    continue
                self._buffer_flatten_export(
                    binding, item_dfrom, item_dto, affected_room_type_id
                )

    @skip_if(lambda self, record, **kwargs: self.no_connector_export(record))
    def on_record_create(self, record, fields=None):
        self._enqueue_pricelist_exports(record)

    @skip_if(lambda self, record, **kwargs: self.no_connector_export(record))
    def on_record_write(self, record, fields=None):
        self._enqueue_pricelist_exports(record)

    @skip_if(lambda self, record, **kwargs: self.no_connector_export(record))
    def on_record_unlink(self, record, fields=None):
        # Read dates now because the row will be gone by precommit time.
        self._enqueue_pricelist_exports(
            record,
            date_from=record.date_start_consumption,
            date_to=record.date_end_consumption,
        )
