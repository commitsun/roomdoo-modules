from odoo import fields, models


class FeatureFlagAddUsersWizard(models.TransientModel):
    _name = "feature.flag.add.users.wizard"
    _description = "Add Feature Flag to Users"

    feature_flag_id = fields.Many2one(
        "feature.flag",
        string="Feature Flag",
        required=True,
        domain=[("is_active_instance", "=", False)],
    )
    user_ids = fields.Many2many("res.users", string="Users", required=True)

    def action_add_flag(self):
        self.feature_flag_id.write(
            {"user_ids": [(4, user.id) for user in self.user_ids]}
        )
        return {"type": "ir.actions.act_window_close"}
