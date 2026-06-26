# Copyright 2026 Roomdoo - Commit[Sun]
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, api, models


class PaymentReportMixin(models.AbstractModel):
    """Row/label builders shared by the PDF and XLSX renderers.

    These live on a model (not a module-level function) so the `_()` calls
    resolve the language from `self.env` — a bare function frame has no `self`
    and would always return the source string untranslated.
    """

    _name = "report.roomdoo_payments_exporter.mixin"
    _description = "Payments report data builder"

    def _payment_type_label(self, payment):
        """Human-readable payment type, derived from the accounting fields.

        Mirrors the API contract (PaymentSummary) without depending on the
        `pms_api_transaction_type` computed field, so the report module stays
        decoupled from the API layer.
        """
        if payment.is_internal_transfer:
            return _("Internal transfer")
        labels = {
            ("inbound", "customer"): _("Customer payment"),
            ("outbound", "customer"): _("Customer refund"),
            ("outbound", "supplier"): _("Supplier payment"),
            ("inbound", "supplier"): _("Supplier refund"),
        }
        return labels.get((payment.payment_type, payment.partner_type), "")

    def _payment_report_rows(self, payments):
        """Flatten payments into the rows shown by the PDF / XLSX report.

        Single source of truth shared by both renderers. New columns are added
        here so PDF and Excel stay in sync.
        """
        rows = []
        for payment in payments:
            currency = payment.currency_id or payment.company_id.currency_id
            # Internal transfers carry the company partner internally; the
            # report shows no contact for them, replicating the listing.
            partner = (
                payment.partner_id
                if payment.partner_id and not payment.is_internal_transfer
                else False
            )
            rows.append(
                {
                    "name": payment.name or "",
                    "date": payment.date,
                    "type_label": self._payment_type_label(payment),
                    "partner_name": partner.display_name if partner else "",
                    "payment_method": payment.payment_method_line_id.name or "",
                    "journal_name": payment.journal_id.name or "",
                    "folio_name": ", ".join(payment.folio_ids.mapped("name")),
                    "ref": payment.ref or "",
                    "created_by": payment.create_uid.name or "",
                    "amount": abs(payment.amount),
                    "currency": currency,
                    "currency_name": currency.name or "",
                }
            )
        return rows


class ReportPayments(models.AbstractModel):
    _name = "report.roomdoo_payments_exporter.report_payments"
    _inherit = "report.roomdoo_payments_exporter.mixin"
    _description = "Payments PDF Report"

    @api.model
    def _get_report_values(self, docids, data=None):
        payments = self.env["account.payment"].browse(docids)
        return {
            "doc_ids": docids,
            "doc_model": "account.payment",
            "docs": payments,
            "rows": self._payment_report_rows(payments),
        }
