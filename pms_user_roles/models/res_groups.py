# Copyright 2026 Commit [Sun]
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
from odoo import api, models


class ResGroups(models.Model):
    _inherit = "res.groups"

    @api.constrains("implied_ids")
    def _constrains_pms_user_roles_implied_ids(self):
        # The implied groups of a role live on its res.groups record, so the
        # role constraint does not see changes made to them. Only the groups
        # of roles are checked: a change in a standard group must never make
        # an unrelated module update fail.
        roles = self.env["res.users.role"].sudo().search([("group_id", "in", self.ids)])
        roles._check_pms_user_roles_groups()
