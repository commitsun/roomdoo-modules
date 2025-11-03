from odoo import api, models


class PmsApiUserRouterHelper(models.AbstractModel):
    _inherit = "pms_api.user_router.helper"

    @api.model
    def extra_features(self):
        res = super().extra_features()
        res.append("lastname2")
        return res
