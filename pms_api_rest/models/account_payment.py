from odoo import api, fields, models


class AccountPayment(models.Model):
    _inherit = "account.payment"

    pms_api_transaction_type = fields.Selection(
        selection=[
            ("customer_inbound", "Customer Payment"),
            ("customer_outbound", "Customer Refund"),
            ("supplier_outbound", "Supplier Payment"),
            ("supplier_inbound", "Supplier Refund"),
            ("internal_transfer", "Internal Transfer"),
        ],
        string="PMS API Transaction Type",
        help="Transaction type for PMS API",
        compute="_compute_pms_api_transaction_type",
    )
    pms_api_counterpart_payment_id = fields.Many2one(
        comodel_name="account.payment",
        string="Int. Transfer Counterpart",
        help="Payment counterpart for internal transfer",
    )

    @api.depends("payment_type", "partner_type")
    def _compute_pms_api_transaction_type(self):
        for record in self:
            if record.is_internal_transfer:
                record.pms_api_transaction_type = "internal_transfer"
            elif record.partner_type == "customer":
                if record.payment_type == "inbound":
                    record.pms_api_transaction_type = "customer_inbound"
                else:
                    record.pms_api_transaction_type = "customer_outbound"
            elif record.partner_type == "supplier":
                if record.payment_type == "outbound":
                    record.pms_api_transaction_type = "supplier_outbound"
                else:
                    record.pms_api_transaction_type = "supplier_inbound"
            else:
                record.pms_api_transaction_type = False

    def write(self, vals):
        res = super().write(vals)
        # The folio decides which Neobookings order the payment belongs to
        if "folio_ids" in vals:
            self.env["pms.neobookings.payment.sync"]._safe_track_payments(self)
        return res

    def unlink(self):
        syncs = (
            self.env["pms.neobookings.payment.sync"]
            .sudo()
            .search([("payment_id", "in", self.ids)])
        )
        payments = self.browse(self.ids)
        res = super().unlink()
        # Neobookings must forget the payments it was told about
        self.env["pms.neobookings.payment.sync"]._safe_track_payments(
            payments, unlinked_syncs=syncs
        )
        return res
