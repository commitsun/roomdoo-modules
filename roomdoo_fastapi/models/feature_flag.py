from odoo import api, fields, models


class FeatureFlag(models.Model):
    _name = "feature.flag"
    _description = "Feature Flag"
    _order = "name"

    name = fields.Char(
        string="Key",
        required=True,
        help="Feature flag identifier used in the front-end application.",
    )
    description = fields.Char(string="Description")
    active = fields.Boolean(default=True)
    is_active_instance = fields.Boolean(
        string="Active for entire instance",
        default=False,
        help="When enabled, this flag is active for all users.",
    )
    user_ids = fields.Many2many(
        "res.users",
        "feature_flag_res_users_rel",
        "flag_id",
        "user_id",
        string="Active for users",
        help="Users for whom this flag is active \
            (ignored when active for entire instance).",
    )

    _sql_constraints = [
        ("unique_name", "unique(name)", "Feature flag key must be unique!"),
    ]

    @api.model
    def get_active_for_user(self, user):
        """Return the list of active feature flag names for the given user."""
        flags = self.search(
            ["|", ("is_active_instance", "=", True), ("user_ids", "in", [user.id])]
        )
        return flags.mapped("name")
