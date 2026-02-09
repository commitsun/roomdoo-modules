from odoo import api, models


class PmsApiFolioRouterHelper(models.AbstractModel):
    _inherit = "pms_api_folio.folio_router.helper"

    @api.model
    def extra_features(self):
        res = super().extra_features()
        res.append("ses_hospedajes")
        return res
