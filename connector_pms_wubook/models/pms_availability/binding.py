# Copyright 2021 Eric Antones <eantones@nuobit.com>
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from psycopg2.extensions import AsIs

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

    @api.depends(
        "odoo_id.real_avail",
        "odoo_id.avail_rule_ids",
        "odoo_id.avail_rule_ids.plan_avail",
        "odoo_id.room_type_id.channel_wubook_bind_ids.default_availability",
    )
    def _compute_sale_avail(self):
        # Cache the authoritative plan per backend: the recordset can span
        # several backends and the lookup hits the parity pricelist binding.
        plan_by_backend = {}
        for record in self:
            backend = record.backend_id
            if backend.id not in plan_by_backend:
                plan_by_backend[backend.id] = backend._get_wubook_availability_plan()
            wubook_plan = plan_by_backend[backend.id]
            # The bookable count shipped to Wubook is driven by a SINGLE
            # plan (the parity pricelist's plan). Every other plan bound
            # to the backend is ignored on purpose: its quota / max_avail
            # never reach Wubook, so there is nothing to reconcile and the
            # connector must not equalize them across plans.
            if wubook_plan:
                rules = record.avail_rule_ids.filtered(
                    lambda x, plan=wubook_plan: x.availability_plan_id == plan
                )
            else:
                # Parity plan not resolvable (misconfigured backend): keep
                # the legacy selection (any plan bound to the backend) but
                # pick one rule deterministically and never write back.
                rules = record.avail_rule_ids.filtered(
                    lambda x, backend=backend: backend
                    in x.availability_plan_id.channel_wubook_bind_ids.backend_id
                ).sorted(key=lambda r: r.availability_plan_id.id)
            if not rules:
                with backend.work_on("channel.wubook.pms.room.type") as work:
                    binder = work.component(usage="binder")
                min_avail = min(
                    record.real_avail,
                    binder.wrap_record(record.room_type_id).default_availability,
                )
                if record.sale_avail != min_avail:
                    record.sale_avail = min_avail
            else:
                sale_avail = rules[:1].plan_avail
                if record.sale_avail != sale_avail:
                    record.sale_avail = sale_avail

    def _inverse_sale_avail(self):
        for record in self:
            if record.sale_avail > record.real_avail:
                # TODO: exportar a wubook el real_avail, corregir wubook
                continue
            # Reflect the value back into the SINGLE authoritative plan
            # (the parity pricelist's plan) only. Other plans bound to the
            # backend are left untouched on purpose. When the parity plan
            # cannot be resolved there is nothing safe to write back.
            wubook_plan = record.backend_id._get_wubook_availability_plan()
            if not wubook_plan:
                continue
            rule = record.avail_rule_ids.filtered(
                lambda x, plan=wubook_plan: x.availability_plan_id == plan
            )
            if rule:
                rule.filtered(
                    lambda x, record=record: x.quota != record.sale_avail
                ).quota = record.sale_avail
            else:
                wubook_plan.write(
                    {
                        "rule_ids": [
                            (
                                0,
                                0,
                                {
                                    "room_type_id": record.room_type_id.id,
                                    "date": record.date,
                                    "pms_property_id": record.pms_property_id.id,
                                    "quota": record.sale_avail,
                                },
                            )
                        ]
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
            query = 'UPDATE "%s" SET "actual_write_date"=%s WHERE id IN %%s' % (
                self._table,
                AsIs("(now() at time zone 'UTC')"),
            )
            for sub_ids in cr.split_for_in_conditions(
                set(self.filtered(lambda i: i.date >= fields.Date.today()).ids)
            ):
                cr.execute(query, [sub_ids])
        res = super()._write(vals)
        return res
