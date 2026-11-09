# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo.addons.component.core import Component
from odoo.addons.component_event.components.event import skip_if
from odoo.addons.queue_job.job import identity_exact

# Sentinel key for the flatten-export buffer.
_FLATTEN_BUFFER_KEY = "connector_pms_wubook.flatten_buffer"

# Sentinel key for the derived room type prices buffer. A room type deriving
# its price from another one has no items of its own on the days it derives
# it, so the regular export never sends those prices: when an item they
# derive from changes, a regular pricelist binding also needs an
# ``export_derived_prices`` job, buffered per binding with the merged date
# window and room types — same coalescence pattern as the flatten buffer.
_DERIVED_BUFFER_KEY = "connector_pms_wubook.derived_prices_buffer"

# Sentinel key for the regular-pricelist export buffer. A *regular* (non
# flatten) pricelist whose item just changed needs a full ``export_record``
# of the binding so the mapper can push only the dirty items via
# ``update_plan_prices``. We buffer one entry per binding and emit a
# single job per binding at precommit — same coalescence pattern as the
# flatten buffer.
_REGULAR_PRICELIST_BUFFER_KEY = "connector_pms_wubook.regular_pricelist_buffer"

# Sentinel key for the per-pricelist staging buffer populated by the
# listener on every write/create/unlink. Resolved **once** at precommit
# by ``_flush_pending_pricelist_items``, which then iterates the
# pricelist's Wubook bindings and flatten descendants a single time per
# pricelist and forwards to ``_FLATTEN_BUFFER_KEY`` / ``_REGULAR_PRICELIST_BUFFER_KEY``
# (the per-binding deduplicating buffers).
#
# Why the indirection: ``pricelist.channel_wubook_bind_ids`` resolves to
# ~one binding per Wubook backend (~90 in Alda prod) and
# ``_get_flatten_descendant_pricelists`` does a SQL search. Doing this
# per item in a 500-item PATCH spent ~25 s in pure listener work,
# blowing through the frontend timeout and triggering retry-driven
# duplicates. Doing it once per pricelist at precommit collapses that
# to sub-second.
_PENDING_PRICELIST_ITEMS_KEY = "connector_pms_wubook.pending_pricelist_items"

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
        # Coarse identity_key per binding so a burst of changes spanning
        # several transactions (e.g. an API import that commits per
        # record) collapses to at most one PENDING job per binding in
        # queue_job. Once that job moves to ``started``, a follow-up
        # change can still enqueue a fresh job — eventual consistency
        # without flooding the queue.
        binding.with_delay(
            identity_key=f"wubook_export_record:{binding._name}:{binding.id}"
        ).export_record(binding.backend_id, binding.odoo_id)


def _flush_derived_buffer(env):
    """Precommit callback: enqueue **one** ``export_derived_prices`` job
    per regular pricelist binding with the merged window and room types.

    Unlike ``export_record``, the job pushes the window and room types it
    is given, so its ``identity_key`` only collapses identical jobs: a
    coarse one would drop the window of a later change while a job is
    still pending.
    """
    data = env.cr.precommit.data.pop(_DERIVED_BUFFER_KEY, None)
    if not data:
        return
    for _binding_id, info in data.items():
        binding = info["binding"].exists()
        if not binding:
            continue
        kwargs = {}
        ranges = info["ranges"]
        # A ``None`` bound means "default window"
        if all(date_from and date_to for date_from, date_to in ranges):
            kwargs["date_from"] = min(date_from for date_from, _dto in ranges)
            kwargs["date_to"] = max(date_to for _dfrom, date_to in ranges)
        room_type_ids = info["room_type_ids"]
        if _ALL_ROOM_TYPES not in room_type_ids:
            kwargs["room_type_ids"] = sorted(room_type_ids)
        binding.with_delay(identity_key=identity_exact).export_derived_prices(**kwargs)


def _flush_pending_pricelist_items(env):
    """Precommit callback: resolve bindings and flatten descendants
    **once per pricelist** and forward to the per-binding flatten /
    regular buffers (which deduplicate and emit a single job each).

    Behavior is equivalent to calling ``_enqueue_pricelist_exports``
    item-by-item: the per-binding buffers were already collapsing N
    item-level requests into one job, so feeding them with a single
    pricelist-level pass yields the same final set of jobs.
    """
    data = env.cr.precommit.data.pop(_PENDING_PRICELIST_ITEMS_KEY, None)
    if not data:
        return
    Pricelist = env["product.pricelist"]
    for pricelist_id, entry in data.items():
        pricelist = Pricelist.browse(pricelist_id).exists()
        if not pricelist:
            continue
        _dispatch_pricelist_exports(env, pricelist, entry)


def _dispatch_pricelist_exports(env, pricelist, entry):
    """Forward an aggregated buffer entry to the per-binding flatten /
    regular / derived prices buffers. Resolves
    ``pricelist.channel_wubook_bind_ids`` and
    ``pricelist._get_flatten_descendant_pricelists()`` once per pricelist.
    """
    is_global = entry["global"]
    property_ids = entry["property_ids"]

    def _in_scope(binding):
        return is_global or (binding.backend_id.pms_property_id.id in property_ids)

    _dispatch_own_bindings(env, pricelist, _in_scope)
    _dispatch_flatten_descendants(env, pricelist, entry, _in_scope)
    _dispatch_derived_prices(env, pricelist, entry, _in_scope)


def _dispatch_own_bindings(env, pricelist, in_scope):
    """Forward to the flatten / regular buffer for the pricelist's own
    Wubook bindings."""
    is_flatten = pricelist.wubook_flatten_to_daily
    for binding in pricelist.channel_wubook_bind_ids:
        if not binding.external_id:
            continue
        if not in_scope(binding):
            continue
        if is_flatten:
            _buffer_flatten_export_at(env, binding, None, None, _ALL_ROOM_TYPES)
        else:
            _buffer_regular_export_at(env, binding)


def _dispatch_flatten_descendants(env, pricelist, entry, in_scope):
    """Forward to the flatten buffer for every binding of the
    pricelist's flatten descendants, scoped by the buffer's aggregated
    date window and room-type set."""
    descendants = pricelist._get_flatten_descendant_pricelists()
    if not descendants:
        return
    date_from = entry["date_from"]
    date_to = entry["date_to"]
    if entry["open_dates"]:
        # An item with no consumption dates moves prices on any day: default
        # window, also when other items of the transaction have dates
        date_from = date_to = None
    room_type_ids = entry["room_type_ids"]
    has_all_rt = _ALL_ROOM_TYPES in room_type_ids
    specific_rt_ids = [rid for rid in room_type_ids if rid is not _ALL_ROOM_TYPES]
    if not has_all_rt:
        # The prices of the room types deriving their price from the changed
        # ones follow them in the descendants too
        specific_rt_ids += _get_price_derivation_descendant_ids(env, specific_rt_ids)
    for descendant in descendants:
        for binding in descendant.channel_wubook_bind_ids:
            if not binding.external_id:
                continue
            if not in_scope(binding):
                continue
            if has_all_rt:
                _buffer_flatten_export_at(
                    env, binding, date_from, date_to, _ALL_ROOM_TYPES
                )
                continue
            for rt_id in specific_rt_ids:
                _buffer_flatten_export_at(env, binding, date_from, date_to, rt_id)


def _buffer_flatten_export_at(env, binding, date_from, date_to, room_type_id):
    """Module-level twin of
    ``ChannelWubookProductPricelistItemListener._buffer_flatten_export``,
    callable from the precommit flush (no ``self``).
    """
    cr = env.cr
    data = cr.precommit.data
    if _FLATTEN_BUFFER_KEY not in data:
        data[_FLATTEN_BUFFER_KEY] = {}
        env_captured = env
        cr.precommit.add(lambda env=env_captured: _flush_flatten_buffer(env))
    entry = data[_FLATTEN_BUFFER_KEY].setdefault(
        binding.id,
        {"binding": binding, "ranges": [], "room_type_ids": set()},
    )
    entry["ranges"].append((date_from, date_to))
    entry["room_type_ids"].add(room_type_id)


def _buffer_regular_export_at(env, binding):
    """Module-level twin of
    ``ChannelWubookProductPricelistItemListener._buffer_regular_export``,
    callable from the precommit flush (no ``self``).
    """
    cr = env.cr
    data = cr.precommit.data
    if _REGULAR_PRICELIST_BUFFER_KEY not in data:
        data[_REGULAR_PRICELIST_BUFFER_KEY] = {}
        env_captured = env
        cr.precommit.add(lambda env=env_captured: _flush_regular_pricelist_buffer(env))
    data[_REGULAR_PRICELIST_BUFFER_KEY].setdefault(binding.id, binding)


def _dispatch_derived_prices(env, pricelist, entry, in_scope):
    """Forward to the derived prices buffer, for the regular standard
    bindings of the pricelist, the room types whose derived prices the
    changed items may have moved, on the window of those items.

    Those are the room types deriving their price in the pricelist from the
    ones of the changed items, at any level, and the latter themselves when
    they derive it: removing one of their own items, an override, makes them
    derive it again on its dates. An item for no room type in particular (a
    global or a category rule) may move any of them. Only the bindings of the
    backends some of them are bound to get the job. Flatten pricelists
    already re-export every room type of their own bindings.
    """
    if pricelist.wubook_flatten_to_daily or pricelist.wubook_plan_type != "standard":
        return
    bindings = pricelist.channel_wubook_bind_ids.filtered(
        lambda binding: binding.external_id and in_scope(binding)
    )
    if not bindings:
        return
    room_types, all_room_types = _get_derived_room_types(
        env, pricelist, entry["room_type_ids"]
    )
    if not room_types:
        return
    # The other backends have no derived price of the pricelist to push
    exported_room_types = env["channel.wubook.pms.room.type"].search(
        [
            ("odoo_id", "in", room_types.ids),
            ("backend_id", "in", bindings.backend_id.ids),
            ("no_export", "=", False),
            ("external_id", "!=", False),
        ]
    )
    bindings = bindings.filtered(
        lambda binding: binding.backend_id in exported_room_types.backend_id
    )
    room_type_ids = {_ALL_ROOM_TYPES} if all_room_types else set(room_types.ids)
    date_from, date_to = entry["date_from"], entry["date_to"]
    if entry["open_dates"]:
        # An item with no consumption dates moves prices on any day
        date_from = date_to = None
    for binding in bindings:
        _buffer_derived_export_at(env, binding, date_from, date_to, room_type_ids)


def _get_derived_room_types(env, pricelist, room_type_ids):
    """Return the room types deriving their price in the pricelist that a
    change of items of ``room_type_ids`` may move, and whether those are all
    the room types deriving their price in it (``_ALL_ROOM_TYPES`` in
    ``room_type_ids``).

    The derivation is configuration: read it whatever the user changing the
    items can access.
    """
    RoomType = env["pms.room.type"].sudo()
    if not RoomType._has_price_derivation():
        return RoomType, False
    if _ALL_ROOM_TYPES in room_type_ids:
        room_types = RoomType.search([("price_base_room_type_id", "!=", False)])
        return room_types._get_price_derivation(pricelist), True
    room_types = RoomType.browse(list(room_type_ids)).exists()
    room_types |= room_types._get_price_derivation_descendants()
    return room_types._get_price_derivation(pricelist), False


def _get_price_derivation_descendant_ids(env, room_type_ids):
    """Return the ids of the room types deriving their price from the given
    ones, at any level."""
    RoomType = env["pms.room.type"].sudo()
    if not RoomType._has_price_derivation():
        return []
    room_types = RoomType.browse(list(room_type_ids)).exists()
    return room_types._get_price_derivation_descendants().ids


def _buffer_derived_export_at(env, binding, date_from, date_to, room_type_ids):
    """Stage an ``export_derived_prices`` of ``room_type_ids`` on the
    binding, merged at precommit with the other ones of the transaction.
    ``(None, None)`` dates stand for the default window and
    ``_ALL_ROOM_TYPES`` for all the room types deriving their price.
    """
    cr = env.cr
    data = cr.precommit.data
    if _DERIVED_BUFFER_KEY not in data:
        data[_DERIVED_BUFFER_KEY] = {}
        env_captured = env
        cr.precommit.add(lambda env=env_captured: _flush_derived_buffer(env))
    entry = data[_DERIVED_BUFFER_KEY].setdefault(
        binding.id,
        {"binding": binding, "ranges": [], "room_type_ids": set()},
    )
    entry["ranges"].append((date_from, date_to))
    entry["room_type_ids"].update(room_type_ids)


def buffer_room_type_prices_export(env, binding, room_types):
    """Stage a re-export of the prices of ``room_types`` on a pricelist
    binding, on the default window, for a change that moves their prices
    but no item (e.g. the derivation of their price from another room type).

    A flatten plan recomputes their prices. A regular standard plan pushes
    the prices of the ones deriving their price in the pricelist: the prices
    of the others come from items, exported as such.
    """
    pricelist = binding.odoo_id
    if pricelist.wubook_flatten_to_daily:
        for room_type in room_types:
            _buffer_flatten_export_at(env, binding, None, None, room_type.id)
        return
    if pricelist.wubook_plan_type != "standard":
        return
    derived = room_types._get_price_derivation(pricelist)
    if derived:
        _buffer_derived_export_at(env, binding, None, None, derived.ids)


class ChannelWubookProductPricelistItemListener(Component):
    """Cascade listener for every ``product.pricelist.item`` change.

    On every write / create / unlink (and before a write moving an item,
    see ``on_pricelist_item_move``), the listener appends a lightweight
    fingerprint to a per-pricelist staging buffer in ``cr.precommit.data``
    and lets a single precommit callback resolve bindings and flatten
    descendants once per pricelist. The actual job-emitting buffers
    (flatten / regular) and their flushes are unchanged.

    Within a single transaction, requests are coalesced through three
    buffers on ``cr.precommit.data`` and flushed in order at precommit
    time:

    1. ``_PENDING_PRICELIST_ITEMS_KEY`` — populated synchronously by
       this listener. One entry per pricelist with the union of
       ``pms_property_ids`` (or a ``global`` flag if any pending item
       has no property restriction), the min/max ``date_start_consumption``
       / ``date_end_consumption`` (and an ``open_dates`` flag if any
       pending item lacks one of them) and the union of affected room
       types. Flushed first.
    2. ``_FLATTEN_BUFFER_KEY``, ``_REGULAR_PRICELIST_BUFFER_KEY`` and
       ``_DERIVED_BUFFER_KEY`` — populated by the precommit flush above.
       One entry per Wubook binding. Flushed last, emit a single queue job
       per binding (the derived prices one only for regular pricelists with
       room types deriving their price from the changed ones).

    Bindings without ``external_id`` are skipped (the wizard's connect
    flow is still in progress).
    """

    _name = "channel.wubook.product.pricelist.item.listener"
    _inherit = "base.connector.listener"
    _apply_on = "product.pricelist.item"

    def _stage_pending_item(
        self,
        record,
        item_property_ids,
        item_date_from,
        item_date_to,
        item_room_type_id,
    ):
        """Append a record's contribution to the per-pricelist staging
        buffer. Cheap (dict / set operations). The expensive binding
        resolution happens later, once per pricelist, at precommit.
        """
        pricelist = record.pricelist_id
        if not pricelist:
            return
        cr = self.env.cr
        data = cr.precommit.data
        if _PENDING_PRICELIST_ITEMS_KEY not in data:
            data[_PENDING_PRICELIST_ITEMS_KEY] = {}
            env = self.env
            cr.precommit.add(lambda env=env: _flush_pending_pricelist_items(env))
        entry = data[_PENDING_PRICELIST_ITEMS_KEY].setdefault(
            pricelist.id,
            {
                "global": False,
                "property_ids": set(),
                "date_from": None,
                "date_to": None,
                "open_dates": False,
                "room_type_ids": set(),
            },
        )
        if not item_property_ids:
            entry["global"] = True
        else:
            entry["property_ids"].update(item_property_ids)
        # An empty date field reads ``False``, not ``None``: an item without
        # one of its consumption dates opens the window and must not reach
        # min() / max() with the dates of the other items
        if not item_date_from or not item_date_to:
            entry["open_dates"] = True
        if item_date_from:
            entry["date_from"] = (
                item_date_from
                if entry["date_from"] is None
                else min(entry["date_from"], item_date_from)
            )
        if item_date_to:
            entry["date_to"] = (
                item_date_to
                if entry["date_to"] is None
                else max(entry["date_to"], item_date_to)
            )
        entry["room_type_ids"].add(item_room_type_id)

    def _stage_from_record(self, record, date_from=None, date_to=None):
        """Read the listener inputs from ``record`` and stage them.

        ``date_from`` / ``date_to`` overrides exist so that
        ``on_record_unlink`` can capture the row's dates before it is
        gone by precommit time.
        """
        item_property_ids = set(record.pms_property_ids.ids)
        room_type = record.product_id.room_type_id if record.product_id else False
        item_room_type_id = room_type.id if room_type else _ALL_ROOM_TYPES
        item_dfrom = record.date_start_consumption if date_from is None else date_from
        item_dto = record.date_end_consumption if date_to is None else date_to
        self._stage_pending_item(
            record, item_property_ids, item_dfrom, item_dto, item_room_type_id
        )

    @skip_if(lambda self, record, **kwargs: self.no_connector_export(record))
    def on_record_create(self, record, fields=None):
        self._stage_from_record(record)

    @skip_if(lambda self, record, **kwargs: self.no_connector_export(record))
    def on_record_write(self, record, fields=None):
        self._stage_from_record(record)

    @skip_if(lambda self, record, **kwargs: self.no_connector_export(record))
    def on_pricelist_item_move(self, record, fields=None):
        """Notified before a write moves the item to other days, room types,
        properties or pricelist (see ``product.pricelist.item.write``).

        Stage the item where it still is: the prices it leaves change as
        well (e.g. a room type derives its price again on the days an
        override leaves), while ``on_record_write`` stages it where it lands.
        """
        self._stage_from_record(record)

    @skip_if(lambda self, record, **kwargs: self.no_connector_export(record))
    def on_record_unlink(self, record, fields=None):
        # Read dates now because the row will be gone by precommit time.
        self._stage_from_record(
            record,
            date_from=record.date_start_consumption,
            date_to=record.date_end_consumption,
        )
