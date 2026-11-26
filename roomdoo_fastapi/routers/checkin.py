from odoo import models


class PmsApiCheckinRouterHelper(models.AbstractModel):
    _inherit = "pms_api_checkin.checkin_router.helper"

    def _context_values(self, folio):
        values = super()._context_values(folio)
        values["brandName"] = (
            self.env["ir.config_parameter"]
            .sudo()
            .get_param("roomdoo_fastapi.instance_name", default="Roomdoo")
        )
        return values
