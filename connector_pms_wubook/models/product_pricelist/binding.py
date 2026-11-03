# Copyright 2021 Eric Antones <eantones@nuobit.com>
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
import logging
from datetime import timedelta

from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models

from odoo.addons.queue_job.job import identity_exact

_logger = logging.getLogger(__name__)

# Wubook rejects dates more than ~2 years ahead. Hard ceiling for any
# window we push (flatten included).
WUBOOK_MAX_DAYS_AHEAD = 730
# Wubook rejects updates of dates more than 2 days back (see
# ``product.pricelist.item.wubook_date_valid``).
WUBOOK_MAX_DAYS_BEHIND = 2
# Wubook rejects prices that are not above this amount.
WUBOOK_MIN_PRICE = 0.01


class ChannelWubookProductPriceBinding(models.Model):
    _name = "channel.wubook.product.pricelist"
    _inherit = "channel.wubook.binding"
    _inherits = {"product.pricelist": "odoo_id"}

    # binding fields
    odoo_id = fields.Many2one(
        comodel_name="product.pricelist",
        string="Odoo ID",
        required=True,
        ondelete="cascade",
    )

    channel_wubook_item_ids = fields.One2many(
        string="Wubook Pricelist Items",
        help="Items in Pricelist",
        comodel_name="channel.wubook.product.pricelist.item",
        inverse_name="channel_wubook_pricelist_id",
    )

    wubook_last_synced_name = fields.Char(
        string="Last name pushed to Wubook",
        readonly=True,
        help=(
            "Snapshot of the pricelist name at the last successful export. "
            "Used by the mapper to skip the ``update_plan_name`` XMLRPC "
            "call when the name has not actually changed (the export is "
            "otherwise triggered by item changes via the scheduler)."
        ),
    )

    def _is_synced_export(self):
        synced = super()._is_synced_export()
        if not synced:
            return False
        wubook_date_valid = fields.Date.today() - relativedelta(days=2)
        room_product_ids = (
            self.env["pms.room.type"]
            .search(
                [
                    ("channel_wubook_bind_ids.backend_id", "=", self.backend_id.id),
                ]
            )
            .mapped("product_id.id")
        )
        self.env.cr.execute(
            """
            SELECT price.id
            FROM product_pricelist_item AS price
                LEFT JOIN channel_wubook_product_pricelist_item AS binding
                    ON binding.odoo_id = price.id
            WHERE price.pricelist_id = %s
            AND price.date_start_consumption >= %s
            AND price.product_id IN %s
            AND EXISTS (
                SELECT 1
                FROM product_pricelist_item_pms_property_rel AS rel
                WHERE rel.product_pricelist_item_id = price.id
                AND rel.pms_property_id = %s
            )
            AND (
                    (
                        binding.backend_id IS NULL
                        OR binding.backend_id != %s
                    )
                OR
                    (
                        binding.backend_id = %s
                        AND (
                            binding.sync_date_export IS NULL
                            OR binding.sync_date_export < binding.actual_write_date
                        )
                    )
                )
            """,
            (
                self.odoo_id.id,
                wubook_date_valid,
                tuple(room_product_ids) if room_product_ids else (0,),
                self.backend_id.pms_property_id.id,
                self.backend_id.id,
                self.backend_id.id,
            ),
        )
        rules_to_export = self.env.cr.fetchone()
        if rules_to_export:
            return False
        return True

    @api.model
    def import_data(
        self,
        backend_id,
        date_from,
        date_to,
        pricelist_ids,
        room_type_ids,
        delayed=True,
    ):
        """Prepare the batch import of Pricelists from Channel"""
        domain = []
        if date_from and date_to:
            domain += [
                ("date", ">=", date_from),
                ("date", "<=", date_to),
            ]
        # TODO: duplicated code, unify
        if pricelist_ids:
            with backend_id.work_on(self._name) as work:
                binder = work.component(usage="binder")
            external_ids = []
            for pl in pricelist_ids:
                binding = binder.wrap_record(pl)
                if not binding or not binding.external_id:
                    raise NotImplementedError(
                        _(
                            "The pricelist %s has no binding. Import of Odoo records "
                            "without binding is not supported yet"
                        )
                        % pl.name
                    )
                external_ids.append(binding.external_id)
            domain.append(("id", "in", external_ids))
        if room_type_ids:
            with backend_id.work_on("channel.wubook.pms.room.type") as work:
                binder = work.component(usage="binder")
            external_ids = []
            for rt in room_type_ids:
                binding = binder.wrap_record(rt)
                if not binding or not binding.external_id:
                    raise NotImplementedError(
                        _(
                            "The Room type %s has no binding. Import of Odoo records "
                            "without binding is not supported yet"
                        )
                        % rt.name
                    )
                external_ids.append(binding.external_id)
            domain.append(("rooms", "in", external_ids))
        return self.import_batch(
            backend_record=backend_id, domain=domain, delayed=delayed
        )

    @api.model
    def export_data(self, backend_record=None):
        """Prepare the batch export of Pricelist to Channel"""
        return self.export_batch(
            backend_record=backend_record,
            domain=[
                ("channel_wubook_bind_ids.backend_id", "in", backend_record.ids),
                "|",
                ("pricelist_type", "=", "daily"),
                ("wubook_flatten_to_daily", "=", True),
                "|",
                ("pms_property_ids", "=", False),
                ("pms_property_ids", "in", backend_record.pms_property_id.ids),
            ],
        )

    # --- Flatten-to-daily helpers ---------------------------------------

    def _get_flatten_export_room_types(self):
        """Return the room types eligible for flatten export on this backend.

        Includes only room types that already have a binding in the same
        backend, since Wubook requires the external id (``rid``) to be set.
        """
        self.ensure_one()
        return self.env["pms.room.type"].search(
            [("channel_wubook_bind_ids.backend_id", "=", self.backend_id.id)]
        )

    def _get_flatten_default_window(self):
        """Default forward window for a flatten export, derived from the
        backend setting and capped by:
        * the latest parent-chain rule date,
        * Wubook's 2-year ceiling (730 days from today).

        Returns ``(date_from, date_to)`` or ``(None, None)`` if window is
        empty/invalid.
        """
        self.ensure_one()
        window = self.backend_id.flatten_window_days or 0
        if window <= 0:
            return None, None
        date_from = fields.Date.today()
        date_to = date_from + relativedelta(days=window - 1)
        chain_max = self.odoo_id._get_flatten_chain_max_date()
        if chain_max and chain_max < date_to:
            date_to = chain_max
        wubook_ceiling = date_from + relativedelta(days=WUBOOK_MAX_DAYS_AHEAD)
        if date_to > wubook_ceiling:
            date_to = wubook_ceiling
        if date_to < date_from:
            return None, None
        return date_from, date_to

    def _compute_flatten_payload_items(
        self, date_from, date_to, room_type_ids=None
    ):
        """Build the adapter-shaped item dicts for a flatten export.

        Each entry is ``{"date": <date>, "price": <float>, "rid": <int>}``,
        matching the format produced by the standard item mapper.

        ``room_type_ids`` restricts the payload to the given subset; if not
        provided, all bound room types of this backend are included. In
        any case the result is intersected with bound room types — we
        never compute prices for room types we can't push to Wubook.
        """
        self.ensure_one()
        if not date_from or not date_to:
            return []
        bound_room_types = self._get_flatten_export_room_types()
        if room_type_ids:
            requested = set(room_type_ids)
            room_types = bound_room_types.filtered(
                lambda rt: rt.id in requested
            )
        else:
            room_types = bound_room_types
        if not room_types:
            return []
        raw = self.odoo_id._compute_flattened_items(
            self.backend_id.pms_property_id, room_types, date_from, date_to
        )
        if not raw:
            return []
        with self.backend_id.work_on("channel.wubook.pms.room.type") as work:
            rt_binder = work.component(usage="binder")
        result = []
        for entry in raw:
            binding = rt_binder.wrap_record(entry["room_type_id"])
            if not binding or not binding.external_id:
                continue
            result.append(
                {
                    "date": entry["date"],
                    "price": entry["fixed_price"],
                    "rid": int(binding.external_id),
                }
            )
        return result

    def export_flattened(self, date_from=None, date_to=None, room_type_ids=None):
        """Push a windowed flatten-to-daily export to Wubook for this binding.

        Called by listeners via ``with_delay()`` whenever an item change
        invalidates a slice of dates on a flatten pricelist (own rule
        change → default window; parent rule change → only the dates of the
        modified parent item).

        If the Wubook plan does not exist yet (no ``external_id``) the call
        falls back to the regular create flow through the mapper, which in
        turn produces synthetic items for the default window.
        """
        self.ensure_one()
        if not self.odoo_id.wubook_flatten_to_daily:
            # Flag was unchecked between enqueue and execution: defer to
            # the regular export flow so nothing surprising happens.
            return self.export_record(self.backend_id, self.odoo_id)
        if not self.external_id:
            return self.export_record(self.backend_id, self.odoo_id)
        if not date_from or not date_to:
            date_from, date_to = self._get_flatten_default_window()
        else:
            chain_max = self.odoo_id._get_flatten_chain_max_date()
            if chain_max and date_to > chain_max:
                date_to = chain_max
            wubook_ceiling = (
                fields.Date.today()
                + relativedelta(days=WUBOOK_MAX_DAYS_AHEAD)
            )
            if date_to > wubook_ceiling:
                date_to = wubook_ceiling
        items = self._compute_flatten_payload_items(
            date_from, date_to, room_type_ids
        )
        if not items:
            return _("Nothing to export")
        with self.backend_id.work_on(self._name) as work:
            adapter = work.component(usage="backend.adapter")
        adapter.write(
            int(self.external_id),
            {"type": "standard", "items": items},
        )
        self.write({"sync_date_export": fields.Datetime.now()})
        return _("Flatten window exported: %s..%s (%d prices)") % (
            date_from,
            date_to,
            len(items),
        )

    # --- Derived room type prices ---------------------------------------
    #
    # A room type can derive its price from another one (see
    # ``pms.room.type.price_base_room_type_id``): on the days the pricelist
    # has no rule for it, its price follows the price of its base room type.
    # It has no items on those days, so the regular export, made of items,
    # never sends those prices to Wubook, which would keep selling the room
    # at whatever price it had. ``export_derived_prices`` pushes them.

    @api.model
    def _get_wubook_window(self, date_from, date_to):
        """Restrict a window of dates to the ones Wubook accepts prices for.

        Returns ``(date_from, date_to)`` or ``(None, None)`` if Wubook
        accepts no date of the window.
        """
        today = fields.Date.today()
        date_from = max(
            fields.Date.to_date(date_from),
            today - relativedelta(days=WUBOOK_MAX_DAYS_BEHIND),
        )
        date_to = min(
            fields.Date.to_date(date_to),
            today + relativedelta(days=WUBOOK_MAX_DAYS_AHEAD),
        )
        if date_to < date_from:
            return None, None
        return date_from, date_to

    def _get_derived_default_window(self):
        """Default window of a push of derived room type prices.

        From today and as long as the flatten window of the backend, within
        the dates Wubook accepts, up to the last day an item of the pricelist
        applies to: past it, no price derives from a rule of the pricelist.

        Returns ``(date_from, date_to)`` or ``(None, None)`` if the window is
        empty.
        """
        self.ensure_one()
        window = self.backend_id.flatten_window_days or 0
        if window <= 0:
            return None, None
        date_from = fields.Date.today()
        date_to = date_from + relativedelta(days=window - 1)
        Item = self.env["product.pricelist.item"]
        item_domain = [("pricelist_id", "=", self.odoo_id.id)]
        if not Item.search_count(item_domain, limit=1):
            return None, None
        # Items with no end date apply to any day after their start
        if not Item.search_count(
            item_domain + [("date_end_consumption", "=", False)], limit=1
        ):
            groups = Item.read_group(item_domain, ["date_end_consumption:max"], [])
            date_to = min(date_to, groups[0]["date_end_consumption"])
        if date_to < date_from:
            return None, None
        return self._get_wubook_window(date_from, date_to)

    def _get_derived_export_room_types(self, room_type_ids=None):
        """Room types whose derived prices this binding pushes to Wubook.

        The room types deriving their price in the pricelist (see
        ``pms.room.type._get_price_derivation``) that the items of the
        pricelist would be exported for: bound to the backend with a Wubook
        id, as long as the binding is exported (``no_export`` ones are left
        out: Wubook ignores the prices of a room anchored to another one),
        and of a room type class synchronized with Wubook.

        :param room_type_ids: restrict the result to these room types
        :returns: dict mapping each room type to its Wubook id (``rid``)
        """
        self.ensure_one()
        if not self.env["pms.room.type"]._has_price_derivation():
            return {}
        backend = self.backend_id
        domain = [
            ("backend_id", "=", backend.id),
            ("no_export", "=", False),
            ("external_id", "!=", False),
            ("odoo_id.price_base_room_type_id", "!=", False),
        ]
        if room_type_ids:
            domain.append(("odoo_id", "in", list(room_type_ids)))
        bindings = self.env["channel.wubook.pms.room.type"].search(domain)
        room_type_classes = backend.backend_type_id.child_id.room_type_class_ids
        nosync_codes = room_type_classes.get_nosync_shortnames()
        room_types = bindings.odoo_id.filtered(
            lambda room_type: room_type.class_id.default_code not in nosync_codes
        )._get_price_derivation(self.odoo_id)
        return {
            binding.odoo_id: int(binding.external_id)
            for binding in bindings
            if binding.external_id and binding.odoo_id in room_types
        }

    def _get_item_days(self, room_types, date_from, date_to):
        """Days the room types have an item of the pricelist that the export
        of the items sends to the plan: a standard item (a fixed price of the
        room type for a day) of the property of the backend.

        That export sends them whether or not the engine applies them (e.g.
        past their sale end date), so no derived price is pushed for those
        days, or the plan would get two prices for the same room and day.

        :returns: set of ``(product id, date)``
        """
        self.ensure_one()
        # Like the engine, which also reads the items of archived pricelists
        items = (
            self.env["product.pricelist.item"]
            .with_context(active_test=False)
            .search(
                [
                    ("pricelist_id", "=", self.odoo_id.id),
                    ("wubook_item_type", "=", "standard"),
                    ("product_id", "in", room_types.product_id.ids),
                    ("date_start_consumption", ">=", date_from),
                    ("date_start_consumption", "<=", date_to),
                    "|",
                    ("pms_property_ids", "=", False),
                    ("pms_property_ids", "in", self.backend_id.pms_property_id.ids),
                ]
            )
        )
        return {(item.product_id.id, item.date_start_consumption) for item in items}

    def _compute_derived_payload_items(self, date_from, date_to, room_type_ids=None):
        """Build the adapter-shaped items of the derived room type prices.

        Each entry is ``{"date": <date>, "price": <float>, "rid": <int>}``,
        matching the format produced by the standard item mapper.

        A room type gets a price on the days it derives its price in the
        pricelist, i.e. the pricelist has no rule for it and no item of the
        room type is exported to the plan (its own items are overrides,
        exported as items: see ``_get_item_days``), and only if that price
        derives, through the chain of its base room types, from a rule of the
        pricelist: never from the list price of a room type, a fallback the
        prices managed in Wubook must not be replaced with. Prices are the
        ones of the engine plus the default board service, like the ones of
        the items (see ``pms.room.type._get_wubook_board_service_amount``),
        rounded to cents. Prices Wubook would reject, not above
        ``WUBOOK_MIN_PRICE``, are left out.

        :param room_type_ids: restrict the push to these room types, all the
            ones of ``_get_derived_export_room_types`` when not given
        :returns: ``(items, skipped)``, the items sorted by date and the
            ``(room type, date)`` pairs left out for their price
        """
        self.ensure_one()
        if not date_from or not date_to or date_to < date_from:
            return [], []
        rids = self._get_derived_export_room_types(room_type_ids)
        if not rids:
            return [], []
        pricelist = self.odoo_id
        pms_property = self.backend_id.pms_property_id
        room_types = self.env["pms.room.type"].browse([rt.id for rt in rids])
        # The base room types the price of each room type derives through,
        # up to the first one not deriving its price in the pricelist
        chains = {}
        for room_type in room_types:
            chain = []
            base = room_type.price_base_room_type_id
            while (
                base
                and base not in chain
                and len(chain) <= room_type._price_derivation_max_depth
            ):
                chain.append(base)
                if not base._get_price_derivation(pricelist):
                    break
                base = base.price_base_room_type_id
            chains[room_type] = chain
        products = room_types.product_id
        for chain in chains.values():
            for base in chain:
                products |= base.product_id
        board_service_amounts = {
            room_type: room_type._get_wubook_board_service_amount(pms_property)
            for room_type in room_types
        }
        item_days = self._get_item_days(room_types, date_from, date_to)
        engine = pricelist.with_context(
            pms_property_id=pms_property.id,
            allowed_pms_property_ids=pms_property.ids,
        )
        now = fields.Datetime.now()
        items, skipped = [], []
        current = date_from
        while current <= date_to:
            # One pass of the engine prices every room type of the chains
            price_map = engine._compute_price_rule(
                products,
                1,
                date=now,
                consumption_date=current,
                pms_property_id=pms_property.id,
            )
            for room_type in room_types:
                price, rule_id = price_map[room_type.product_id.id]
                if rule_id or (room_type.product_id.id, current) in item_days:
                    # A rule of the pricelist for the room type (no derivation)
                    # or an item of it, exported as such even if the engine
                    # does not apply it
                    continue
                # The price comes from the first room type of the chain with
                # a rule that day (its item, an override, a generic rule)
                for base in chains[room_type]:
                    if price_map[base.product_id.id][1]:
                        break
                else:
                    # No rule up the chain: derived from a list price
                    continue
                # To the cent, with no floating point noise in the payload
                wubook_price = round(
                    pricelist.currency_id.round(
                        price + board_service_amounts[room_type]
                    ),
                    2,
                )
                if price <= WUBOOK_MIN_PRICE or wubook_price <= WUBOOK_MIN_PRICE:
                    skipped.append((room_type, current))
                    continue
                items.append(
                    {
                        "date": current,
                        "price": wubook_price,
                        "rid": rids[room_type],
                    }
                )
            current += timedelta(days=1)
        return items, skipped

    def export_derived_prices(self, date_from=None, date_to=None, room_type_ids=None):
        """Push to Wubook the prices of the room types deriving their price.

        Pushes, with ``update_plan_prices``, the prices of the room types on
        the days they derive their price from a rule of the pricelist (see
        ``_compute_derived_payload_items``). Called via ``with_delay()`` by
        the listeners when something those prices derive from changes (an
        item of the pricelist, the derivation of a room type), on connecting
        a room type and on exporting the pricelists of the backend. Flatten
        plans compute every price already, the derived ones included.

        :param date_from: first day to push
        :param date_to: last day to push; without both dates, the default
            window (see ``_get_derived_default_window``)
        :param room_type_ids: ids of the room types to push, all the room
            types deriving their price in the pricelist when not given
        """
        self.ensure_one()
        pricelist = self.odoo_id
        if pricelist.wubook_flatten_to_daily:
            # Flag set between enqueue and execution
            return self.export_flattened(date_from, date_to, room_type_ids)
        if not self.external_id or pricelist.wubook_plan_type != "standard":
            return _("No derived room type prices to export")
        if date_from and date_to:
            date_from, date_to = self._get_wubook_window(date_from, date_to)
        else:
            date_from, date_to = self._get_derived_default_window()
        items, skipped = self._compute_derived_payload_items(
            date_from, date_to, room_type_ids
        )
        if skipped:
            _logger.warning(
                "Derived prices of %s not exported to the Wubook plan %s on %d "
                "days from %s to %s: Wubook rejects prices not above %s",
                ", ".join(sorted({rt.display_name for rt, _day in skipped})),
                self.external_id,
                len(skipped),
                min(day for _rt, day in skipped),
                max(day for _rt, day in skipped),
                WUBOOK_MIN_PRICE,
            )
        if items:
            with self.backend_id.work_on(self._name) as work:
                adapter = work.component(usage="backend.adapter")
            adapter.write(
                int(self.external_id),
                {"type": "standard", "items": items},
            )
            result = _(
                "Derived room type prices exported: %(date_from)s..%(date_to)s "
                "(%(count)s prices)",
                date_from=date_from,
                date_to=date_to,
                count=len(items),
            )
        else:
            result = _("No derived room type prices to export")
        if skipped:
            result += "\n" + _(
                "%(count)s prices not above %(min_price)s, which Wubook rejects, "
                "were left out",
                count=len(skipped),
                min_price=WUBOOK_MIN_PRICE,
            )
        return result

    def _export_derived_prices_delayed(self, room_type_ids=None):
        """Enqueue ``export_derived_prices`` on the bindings of self that
        have derived room type prices to push: the regular standard plans
        exported to Wubook with room types deriving their price in them.

        :param room_type_ids: restrict the push to these room types
        """
        for binding in self:
            pricelist = binding.odoo_id
            if (
                not binding.external_id
                or pricelist.wubook_flatten_to_daily
                or pricelist.wubook_plan_type != "standard"
                or not binding._get_derived_export_room_types(room_type_ids)
            ):
                continue
            kwargs = {}
            if room_type_ids:
                kwargs["room_type_ids"] = sorted(room_type_ids)
            binding.with_delay(identity_key=identity_exact).export_derived_prices(
                **kwargs
            )

    def resync_import(self):
        for record in self:
            room_type_items = record.item_ids.filtered(
                lambda x: not x.pms_property_ids
                or self.backend_id.pms_property_id in x.pms_property_ids
            )
            if room_type_items:
                date_from = min(room_type_items.mapped("date_start_consumption"))
                date_to = max(room_type_items.mapped("date_end_consumption"))
                products = room_type_items.mapped("product_id")
                room_types = self.env["pms.room.type"].search(
                    [
                        ("product_id", "in", products.ids),
                    ]
                )
                record.import_data(
                    self.backend_id,
                    date_from,
                    date_to,
                    self.odoo_id,
                    room_types,
                    delayed=False,
                )

    # def write(self, values):
    #     # workaround to surpass an Odoo bug in a delegation inheritance
    #     # of product.pricelist that does not let to write 'name' field
    #     # if 'items_ids' is written as well on the same write call.
    #     # With other fields like 'sequence' it does not crash but it does not
    #     # save the value entered. For other fields it works but it's unstable.
    #     item_ids = values.pop("item_ids", None)
    #     if item_ids:
    #         super(ChannelWubookProductPriceBinding, self).write({"item_ids": item_ids})
    #     if values:
    #         return super(ChannelWubookProductPriceBinding, self).write(values)
