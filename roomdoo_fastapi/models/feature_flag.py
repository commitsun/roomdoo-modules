import secrets

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
        help="When enabled, this flag is active for all users. "
        "Enabling it logs every user out of the PMS.",
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

    @api.model_create_multi
    def create(self, vals_list):
        flags = super().create(vals_list)
        if any(flags.mapped("is_active_instance")):
            self._renew_jwt_secrets()
        return flags

    def write(self, vals):
        activated = vals.get("is_active_instance") and self.filtered(
            lambda flag: not flag.is_active_instance
        )
        res = super().write(vals)
        if activated:
            self._renew_jwt_secrets()
        return res

    @api.model
    def _renew_jwt_secrets(self):
        """Invalidate every issued token, so all users have to log in again."""
        validator = (
            self.env["auth.jwt.validator"].sudo()._get_validator_by_name("api_pms")
        )
        validator.write(
            {
                "secret_key": secrets.token_hex(32),
                "refresh_token_secret": secrets.token_hex(32),
            }
        )

    @api.model
    def get_active_for_user(self, user):
        """Return the list of active feature flag names for the given user."""
        flags = self.search(
            ["|", ("is_active_instance", "=", True), ("user_ids", "in", [user.id])]
        )
        return flags.mapped("name")
