# Copyright 2021 Eric Antones <eantones@nuobit.com>
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models

# Wubook rejects dates more than ~2 years ahead. Hard ceiling for any
# window we push (flatten included).
WUBOOK_MAX_DAYS_AHEAD = 730


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
