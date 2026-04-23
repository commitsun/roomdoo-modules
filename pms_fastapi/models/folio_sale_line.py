from odoo import api, fields, models


class FolioSaleLine(models.Model):
    _inherit = "folio.sale.line"

    amount_to_invoice = fields.Monetary(
        help="Remaining amount to invoice on the folio line (taxes included)",
        compute="_compute_amount_to_invoice",
        compute_sudo=True,
    )

    @api.depends("qty_to_invoice", "price_reduce", "tax_ids", "product_uom_qty")
    def _compute_amount_to_invoice(self):
        for line in self:
            if not line.qty_to_invoice:
                line.amount_to_invoice = 0.0
                continue
            taxes = line.tax_ids.compute_all(
                line.price_reduce,
                currency=line.currency_id,
                quantity=line.qty_to_invoice,
                product=line.product_id,
                partner=line.folio_id.partner_id,
            )
            line.amount_to_invoice = taxes["total_included"]
