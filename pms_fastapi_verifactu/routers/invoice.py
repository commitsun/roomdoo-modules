from odoo import api, models


class PmsApiInvoiceRouterHelper(models.AbstractModel):
    _inherit = "pms_api_invoice.invoice_router.helper"

    @api.model
    def extra_features(self):
        res = super().extra_features()
        res.append("l10n_es_verifactu_oca")
        return res
