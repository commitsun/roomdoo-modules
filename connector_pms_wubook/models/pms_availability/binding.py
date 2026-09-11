# Copyright 2021 Eric Antones <eantones@nuobit.com>
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import api, fields, models

AUTO_EXPORT_FIELDS = [
    "sale_avail",
]


class ChannelWubookPmsAvailabilityBinding(models.Model):
    _name = "channel.wubook.pms.availability"
    _inherit = "channel.wubook.binding"
    _inherits = {"pms.availability": "odoo_id"}

    external_id = fields.Char(string="External ID")

    odoo_id = fields.Many2one(
        comodel_name="pms.availability",
        string="Odoo ID",
        required=True,
        ondelete="cascade",
    )

    channel_wubook_property_availability_id = fields.Many2one(
        comodel_name="channel.wubook.pms.property.availability",
        string="Wubook Property",
        required=True,
        ondelete="cascade",
    )

    sale_avail = fields.Integer(
        store=True,
        compute="_compute_sale_avail",
        inverse="_inverse_sale_avail",
    )

    # NOTE: the inventory lives in ``pms.inventory.rule``, with no relation
    # to ``pms.availability``, so there is no field path to declare here. A
    # rule reaches this compute through the listener on that model.
    @api.depends(
        "odoo_id.real_avail",
        "odoo_id.room_type_id.channel_wubook_bind_ids.default_availability",
    )
    def _compute_sale_avail(self):
        # The bookable count shipped to Wubook is the availability of the
        # PROPERTY, so the inventory is resolved at the general scope: Wubook
        # is not a sale channel of its own here.
        caps_by_property = {}
        keyed = self.filtered(lambda record: record.date and record.pms_property_id)
        for pms_property in keyed.mapped("pms_property_id"):
            records = keyed.filtered(
                lambda record, prop=pms_property: record.pms_property_id == prop
            )
            dates = records.mapped("date")
            caps_by_property[pms_property.id] = self.env[
                "pms.inventory.rule"
            ].get_inventory_caps(
                pms_property.id,
                min(dates),
                max(dates),
                room_type_ids=records.mapped("room_type_id").ids,
            )
        for record in self:
            cap = caps_by_property.get(record.pms_property_id.id, {}).get(
                (record.room_type_id.id, record.date)
            )
            if cap is None:
                # Nothing declared for this night: fall back to the
                # availability the connector defaults to for the room type.
                with record.backend_id.work_on("channel.wubook.pms.room.type") as work:
                    binder = work.component(usage="binder")
                sale_avail = min(
                    record.real_avail,
                    binder.wrap_record(record.room_type_id).default_availability,
                )
            else:
                sale_avail = min(record.real_avail, cap)
            if record.sale_avail != sale_avail:
                record.sale_avail = sale_avail

    def _inverse_sale_avail(self):
        for record in self:
            if record.sale_avail > record.real_avail:
                # TODO: exportar a wubook el real_avail, corregir wubook
                continue
            # A single night rule at the general scope, on ``max_avail`` and
            # NOT on ``quota``: what Wubook calls ``avail`` is how many rooms
            # can be sold right now, and a quota has the nights already sold
            # subtracted from it, so a quota of 3 with 2 sold would leave 1
            # sellable, which is not what Wubook said. It also keeps the round
            # trip idempotent.
            rule = self.env["pms.inventory.rule"].search(
                [
                    ("pms_property_id", "=", record.pms_property_id.id),
                    ("room_type_id", "=", record.room_type_id.id),
                    ("sale_channel_id", "=", False),
                    ("agency_id", "=", False),
                    ("date_from", "=", record.date),
                    ("date_to", "=", record.date),
                ],
                limit=1,
            )
            if rule:
                if rule.max_avail != record.sale_avail:
                    rule.max_avail = record.sale_avail
            else:
                self.env["pms.inventory.rule"].create(
                    {
                        "pms_property_id": record.pms_property_id.id,
                        "room_type_id": record.room_type_id.id,
                        "date_from": record.date,
                        "date_to": record.date,
                        "max_avail": record.sale_avail,
                    }
                )

    @api.model
    def export_data(self, backend_id, date_from, date_to, room_type_ids):
        """Prepare the batch export records to Backend"""
        domain = [("pms_property_id", "=", backend_id.pms_property_id.id)]
        if date_from and date_to:
            domain += [("date", ">=", date_from), ("date", "<=", date_to)]
        if room_type_ids:
            domain += [("room_type_id", "in", room_type_ids.ids)]
        return self.export_batch(backend_record=backend_id, domain=domain)

    @api.model
    def create(self, vals):
        backend = self.backend_id.browse(vals["backend_id"])
        with backend.work_on(
            self.channel_wubook_property_availability_id._name
        ) as work:
            binder = work.component(usage="binder")
        binding = binder.wrap_record(
            self.odoo_id.browse(vals["odoo_id"]).pms_property_id
        )
        vals["channel_wubook_property_availability_id"] = binding.id
        binding = super().create(vals)
        # channel_wubook_availability_id = vals.get(
        #     "channel_wubook_availability_id"
        # )
        # if channel_wubook_availability_id:
        #     binding = self.channel_wubook_availability_id.browse(
        #         channel_wubook_availability_id
        #     )
        #     vals["availability_id"] = binding.odoo_id.id
        # else:
        #     # TODO: put this code on mapper???? Is it possible??
        #     backend = self.backend_id.browse(vals["backend_id"])
        #     with backend.work_on(
        #         self.channel_wubook_availability_id._name
        #     ) as work:
        #         binder = work.component(usage="binder")
        #     binding = binder.wrap_record(
        #         self.odoo_id.browse(vals["odoo_id"]).availability_plan_id
        #     )
        #     vals["channel_wubook_availability_id"] = binding.id
        #     binding = super().create(vals)
        return binding

    def _write(self, vals):
        cr = self._cr
        if any([field in vals for field in AUTO_EXPORT_FIELDS]):
            query = (
                f'UPDATE "{self._table}" '
                "SET \"actual_write_date\"=(now() at time zone 'UTC') "
                "WHERE id IN %s"
            )
            for sub_ids in cr.split_for_in_conditions(
                set(self.filtered(lambda i: i.date >= fields.Date.today()).ids)
            ):
                cr.execute(query, [sub_ids])
        res = super()._write(vals)
        return res
