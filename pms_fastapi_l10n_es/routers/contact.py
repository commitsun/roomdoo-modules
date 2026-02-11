from odoo import api, models


class PmsApiL10nEsContactRouterHelper(models.AbstractModel):
    _inherit = "pms_api_contact.contact_router.helper"

    @api.model
    def extra_features(self):
        res = super().extra_features()
        res.append("comercial_name")
        return res
