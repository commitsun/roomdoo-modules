# Copyright 2026 Commit [Sun]
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
from odoo import api, models


class PmsProperty(models.Model):
    _inherit = "pms.property"

    @api.model_create_multi
    def create(self, vals_list):
        properties = super().create(vals_list)
        # Holders of an "All properties" role must not miss a new property.
        lines = (
            self.env["res.users.role.line"]
            .sudo()
            .search([("role_id.pms_all_properties", "=", True)])
        )
        lines.filtered("is_enabled").user_id._pms_user_roles_sync_property_scope()
        return properties
