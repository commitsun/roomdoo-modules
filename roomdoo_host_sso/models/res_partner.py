from odoo import models


class ResPartner(models.Model):
    _inherit = "res.partner"

    def _compute_signup_valid(self):
        res = super()._compute_signup_valid()
        # Some modules take a valid sign-up link as a password: the Roomdoo
        # team never has one, whatever is sent to them.
        for partner in self:
            users = partner.sudo().with_context(active_test=False).user_ids
            if users.filtered("host_sso_user"):
                partner.signup_valid = False
        return res
