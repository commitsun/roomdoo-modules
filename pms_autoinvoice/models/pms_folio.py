import datetime

from dateutil.relativedelta import relativedelta

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
        # Add the reservation section header of every line being invoiced. The
        # section name carries the room ("folio/reservation - room"), which is
        # the reference corporate clients check the invoice against, and
        # get_invoice_vals_list can only emit a section that is part of the
        # lines to invoice (this is what manual invoicing does as well).
        # Only sections billed to the same partner as the line are added, so a
        # section can never end up alone in a group and create an empty invoice.
        for line in self.env["folio.sale.line"].browse(list(lines_to_invoice)):
            section = line.section_id
            if (
                section
                and section.id not in lines_to_invoice
                and section.default_invoice_to == line.default_invoice_to
            ):
                lines_to_invoice[section.id] = 0
        return lines_to_invoice

    def _get_invoice_date(self, partner_invoice_id, lines_to_invoice, date=None):
        invoice_date = super()._get_invoice_date(
            partner_invoice_id, lines_to_invoice, date=date
        )
        # The invoicing policy only dates the invoices issued by the automatic
        # invoicing cron. Manual invoicing (backend wizard, app) must keep the
        # date asked for by the caller, which is usually none: the invoice then
        # takes the date of the day it is posted. Dating a manual invoice on the
        # checkout of the reservation books it in a past period -- often locked,
        # or already closed -- and leaves a draft that cannot be posted, because
        # the sequence of the current period does not match that date.
        if not self.env.context.get("autoinvoice"):
            return invoice_date
        partner_invoice = self.env["res.partner"].browse(partner_invoice_id)
        partner_invoice_policy = self.pms_property_id.default_invoicing_policy
        if partner_invoice and partner_invoice.invoicing_policy != "property":
            partner_invoice_policy = partner_invoice.invoicing_policy
        if partner_invoice_policy == "checkout":
            margin_days_autoinvoice = (
                self.pms_property_id.margin_days_autoinvoice
                if partner_invoice.margin_days_autoinvoice == 0
                else partner_invoice.margin_days_autoinvoice
            )
            checkouts = (
                self.env["pms.reservation"]
                .search([("sale_line_ids", "in", list(lines_to_invoice.keys()))])
                .mapped("checkout")
            )
            # Folios billing only services not bound to a reservation have no
            # checkout to work with: keep the date computed by super() instead
            # of blowing up on an empty max().
            if checkouts:
                invoice_date = max(checkouts) + datetime.timedelta(
                    days=margin_days_autoinvoice
                )
        if partner_invoice_policy == "month_day":
            month_day = (
                self.pms_property_id.invoicing_month_day
                if partner_invoice.invoicing_month_day == 0
                else partner_invoice.invoicing_month_day
            )
            today = fields.Date.today()
            # relativedelta clamps the day to the end of the month, and rolls
            # the year over in December (month + 1 would raise there).
            invoice_date = today + relativedelta(day=month_day)
            if invoice_date < today:
                invoice_date = today + relativedelta(months=1, day=month_day)
        return invoice_date
