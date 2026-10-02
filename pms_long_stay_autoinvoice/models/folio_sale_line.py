from datetime import date

from dateutil.relativedelta import relativedelta

from odoo import api, models


class FolioSaleLine(models.Model):
    _inherit = "folio.sale.line"

    @api.depends(
        "default_invoice_to",
        "invoice_status",
        "folio_id.last_checkout",
        "reservation_id.checkout",
        "service_id.reservation_id.checkout",
        # New dependencies introduced by this bridge — the parent compute
        # ignores these so we must re-declare the full list to keep them in
        # sync with our override of ``_get_to_invoice_date``.
        "reservation_id.reservation_type",
        "product_id",
        "product_id.product_tmpl_id.is_long_stay_product",
        "service_line_ids.date",
    )
    def _compute_autoinvoice_date(self):
        return super()._compute_autoinvoice_date()

    def _get_to_invoice_date(self):
        """Residence-specific schedule for long-stay reservations.

        - Pernocta line (priced 0 € on long-stay): never autoinvoice.
        - Service line whose product is the long-stay product
          (the monthly 'pernocta as a service'): day 1 of the month of the
          reservation check-in.
        - Service line for any other product (medical, laundry, ...): day 1
          of the month *following* the service line's own date.
        - Anything else: standard ``pms_autoinvoice`` logic.
        """
        self.ensure_one()
        reservation = self.reservation_id
        if not reservation or reservation.reservation_type != "long_stay":
            return super()._get_to_invoice_date()

        if not self.service_id and self.reservation_line_ids:
            return False

        product_tmpl = self.product_id.product_tmpl_id
        if getattr(product_tmpl, "is_long_stay_product", False):
            checkin = reservation.checkin
            return date(checkin.year, checkin.month, 1)

        service_line = self.service_line_ids[:1]
        ref_date = service_line.date if service_line else reservation.checkin
        next_month = ref_date + relativedelta(months=1)
        return date(next_month.year, next_month.month, 1)
