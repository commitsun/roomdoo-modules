import datetime

from odoo import fields, models


class PmsFolio(models.Model):
    _inherit = "pms.folio"

    def _get_lines_to_invoice(self, final=False):
        self = self.with_context(lines_auto_add=True)
        res = super()._get_lines_to_invoice(final=final)
        if not self._context.get("autoinvoice"):
            return res
        due_lines = self.sale_line_ids.filtered(
            lambda r: r.autoinvoice_date
            and r.autoinvoice_date <= fields.Date.today()
            and (
                r.qty_to_invoice > 0
                or (r.qty_to_invoice < 0 and final)
                or r.display_type == "line_note"
            )
        )
        # Drop line_notes whose partner_invoice has no billable line in this
        # batch — otherwise _create_invoices would group them into a 0€
        # invoice for that partner (e.g. late reservations added as orphan
        # notes after the original folio was already invoiced).
        billable_partner_ids = {
            line.default_invoice_to.id for line in due_lines if not line.display_type
        }
        lines_to_invoice = dict()
        for line in due_lines:
            if (
                line.display_type == "line_note"
                and line.default_invoice_to.id not in billable_partner_ids
            ):
                continue
            lines_to_invoice[line.id] = 0 if line.display_type else line.qty_to_invoice
        return lines_to_invoice

    def _get_invoice_date(self, partner_invoice_id, lines_to_invoice, date=None):
        partner_invoice = self.env["res.partner"].browse(partner_invoice_id)
        partner_invoice_policy = self.pms_property_id.default_invoicing_policy
        if partner_invoice and partner_invoice.invoicing_policy != "property":
            partner_invoice_policy = partner_invoice.invoicing_policy
        invoice_date = super()._get_invoice_date(
            partner_invoice_id, lines_to_invoice, date=date
        )
        if partner_invoice_policy == "checkout":
            margin_days_autoinvoice = (
                self.pms_property_id.margin_days_autoinvoice
                if partner_invoice.margin_days_autoinvoice == 0
                else partner_invoice.margin_days_autoinvoice
            )
            invoice_date = max(
                self.env["pms.reservation"]
                .search([("sale_line_ids", "in", lines_to_invoice.keys())])
                .mapped("checkout")
            ) + datetime.timedelta(days=margin_days_autoinvoice)
        if partner_invoice_policy == "month_day":
            month_day = (
                self.pms_property_id.invoicing_month_day
                if partner_invoice.invoicing_month_day == 0
                else partner_invoice.invoicing_month_day
            )
            invoice_date = datetime.date(
                datetime.date.today().year,
                datetime.date.today().month,
                month_day,
            )
            if invoice_date < datetime.date.today():
                invoice_date = datetime.date(
                    datetime.date.today().year,
                    datetime.date.today().month + 1,
                    month_day,
                )
        return invoice_date
