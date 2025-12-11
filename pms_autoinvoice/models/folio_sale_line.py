from datetime import timedelta

from dateutil import relativedelta

from odoo import api, fields, models


class FolioSaleLine(models.Model):
    _inherit = "folio.sale.line"

    autoinvoice_date = fields.Date(
        compute="_compute_autoinvoice_date",
        store=True,
    )

    @api.depends(
        "default_invoice_to",
        "invoice_status",
        "folio_id.last_checkout",
        "reservation_id.checkout",
        "service_id.reservation_id.checkout",
    )
    def _compute_autoinvoice_date(self):
        for record in self:
            record.autoinvoice_date = record._get_to_invoice_date()

    def _get_to_invoice_date(self):
        self.ensure_one()
        partner = self.default_invoice_to
        if self.reservation_id:
            last_checkout = self.reservation_id.checkout
        elif self.service_id and self.service_id.reservation_id:
            last_checkout = self.service_id.reservation_id.checkout
        else:
            last_checkout = self.folio_id.last_checkout
        if not last_checkout:
            return False
        invoicing_policy = (
            self.folio_id.pms_property_id.default_invoicing_policy
            if not partner or partner.invoicing_policy == "property"
            else partner.invoicing_policy
        )
        if invoicing_policy == "manual":
            return False
        if invoicing_policy == "checkout":
            margin_days = (
                self.folio_id.pms_property_id.margin_days_autoinvoice
                if not partner or partner.invoicing_policy == "property"
                else partner.margin_days_autoinvoice
            )
            return last_checkout + timedelta(days=margin_days)
        if invoicing_policy == "month_day":
            month_day = (
                self.folio_id.pms_property_id.invoicing_month_day
                if not partner or partner.invoicing_policy == "property"
                else partner.invoicing_month_day
            )
            if last_checkout.day <= month_day:
                return last_checkout.replace(day=month_day)
            else:
                return (last_checkout + relativedelta.relativedelta(months=1)).replace(
                    day=month_day
                )
