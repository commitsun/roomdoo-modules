from odoo import fields, models


class RoomdooHostSsoProfile(models.Model):
    """What the users host creates here may do, by the profile host sends."""

    _name = "roomdoo.host.sso.profile"
    _description = "Profile of the users host creates"
    _order = "name"

    code = fields.Char(required=True, help="The profile host sends in its tokens.")
    name = fields.Char(required=True, translate=True)
    group_ids = fields.Many2many("res.groups", string="Groups")

    _sql_constraints = [("code_unique", "unique(code)", "Profile codes are unique.")]
