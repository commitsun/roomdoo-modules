# Copyright 2021 Eric Antones <eantones@nuobit.com>
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
import logging

from psycopg2.extensions import AsIs

from odoo import fields, models

_logger = logging.getLogger(__name__)

AUTO_EXPORT_FIELDS = [
    "min_stay",
    "max_stay",
    "closed",
    "min_stay_arrival",
    "max_stay_arrival",
    "closed_arrival",
    "closed_departure",
]


class PmsRoomTypeAvailabilityRule(models.Model):
    _inherit = "pms.availability.plan.rule"

    channel_wubook_bind_ids = fields.One2many(
        comodel_name="channel.wubook.pms.availability.plan.rule",
        inverse_name="odoo_id",
        string="Channel Wubook PMS Bindings",
    )

    def wubook_date_valid(self):
        if not self.date:
            return False
        age = (fields.Date.today() - self.date).days
        # Lower bound: WuBook rejects updates older than 2 days.
        # Upper bound: WuBook also rejects dates more than ~2 years ahead.
        return -730 <= age <= 2

    def _write(self, vals):
        cr = self._cr
        if any([field in vals for field in AUTO_EXPORT_FIELDS]):
            query = (
                'UPDATE "channel_wubook_pms_availability_plan_rule" '
                'SET "actual_write_date"=%s WHERE odoo_id IN %%s'
                % (AsIs("(now() at time zone 'UTC')"))
            )
            for sub_ids in cr.split_for_in_conditions(set(self.ids)):
                cr.execute(query, [sub_ids])
        res = super()._write(vals)
        return res
