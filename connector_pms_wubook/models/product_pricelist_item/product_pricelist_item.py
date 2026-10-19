# Copyright 2021 Eric Antones <eantones@nuobit.com>
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
from psycopg2.extensions import AsIs

from odoo import api, fields, models

AUTO_EXPORT_FIELDS = [
    "fixed_price",
]

# Fields placing an item on some days, room types, properties and pricelist:
# writing them moves the item, which changes the prices it leaves as well
SCOPE_FIELDS = {
    "pricelist_id",
    "product_id",
    "pms_property_ids",
    "date_start_consumption",
    "date_end_consumption",
}


class ProductPricelistItem(models.Model):
    _inherit = "product.pricelist.item"

    channel_wubook_bind_ids = fields.One2many(
        comodel_name="channel.wubook.product.pricelist.item",
        inverse_name="odoo_id",
        string="Channel Wubook PMS Bindings",
    )

    wubook_item_type = fields.Selection(
        selection=[("virtual", "Virtual"), ("standard", "Standard")],
        readonly=True,
        store=True,
        compute="_compute_wubook_item_type",
    )

    @api.depends("applied_on", "compute_price", "base")
    def _compute_wubook_item_type(self):
        for rec in self:
            if (rec.applied_on, rec.compute_price, rec.base) == (
                "3_global",
                "formula",
                "pricelist",
            ):
                rec.wubook_item_type = "virtual"
            elif (rec.applied_on, rec.compute_price) == ("0_product_variant", "fixed"):
                rec.wubook_item_type = "standard"
            else:
                rec.wubook_item_type = False

    def wubook_date_valid(self):
        if not self.date_start_consumption:
            return False
        today = fields.Date.today()
        age = (today - self.date_start_consumption).days
        # Wubook rejects updates older than 2 days. It also refuses dates
        # more than ~2 years (730 days) in the future — surface both bounds
        # so the mapper's ``skip_item`` filters them out instead of letting
        # the XMLRPC call fail.
        return -730 <= age <= 2

    def write(self, vals):
        if SCOPE_FIELDS.intersection(vals):
            # ``on_record_write`` only sees where the items land: let the
            # listeners know where they are before they move
            event = self._event("on_pricelist_item_move")
            for record in self:
                event.notify(record, fields=list(vals))
        return super().write(vals)

    def _write(self, vals):
        cr = self._cr
        if any([field in vals for field in AUTO_EXPORT_FIELDS]):
            query = (
                'UPDATE "channel_wubook_product_pricelist_item" '
                'SET "actual_write_date"=%s WHERE odoo_id IN %%s'
                % (AsIs("(now() at time zone 'UTC')"))
            )
            for sub_ids in cr.split_for_in_conditions(set(self.ids)):
                cr.execute(query, [sub_ids])
        res = super()._write(vals)
        return res
