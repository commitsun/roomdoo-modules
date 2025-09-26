from odoo import api, models


class PmsApiContactRouterHelper(models.AbstractModel):
    _inherit = "pms_api_contact.contact_router.helper"

    @api.model
    def extra_features(self):
        res = super().extra_features()
        res.append("lastname2")
        return res
