# Copyright 2026 Commit [Sun]
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
from odoo import _, api, fields, models
from odoo.exceptions import UserError

PASSWORD_FIELDS = ("current_password", "new_password", "confirm_password")
OPERATION_LOG_ACTIONS = {
    "set": "set_password",
    "change": "change_password",
    "reset": "reset_password",
}


class PmsPaymentCardPasswordWizard(models.TransientModel):
    """Create, change or reset the card password of a property.

    The passwords are never stored. The client saves a wizard before calling
    its button, in a request of its own, so a stored password would stay in
    the table until the transient records are cleaned up. The operation runs
    when the wizard is saved instead, with the passwords taken out of the
    values first, and the button only closes the dialog.
    """

    _name = "pms.payment.card.password.wizard"
    _description = "Card password"

    pms_property_id = fields.Many2one(
        comodel_name="pms.property",
        required=True,
        readonly=True,
    )
    password_configured = fields.Boolean(
        related="pms_property_id.payment_card_password_configured",
    )
    operation = fields.Selection(
        selection=[
            ("set", "Create the password"),
            ("change", "Change the password"),
            ("reset", "Reset a forgotten password"),
        ],
        required=True,
    )
    current_password = fields.Char()
    new_password = fields.Char()
    confirm_password = fields.Char()

    @api.model
    def default_get(self, fields_list):
        values = super().default_get(fields_list)
        pms_property = self.env["pms.property"].browse(values.get("pms_property_id"))
        if "operation" in fields_list and pms_property:
            configured = pms_property.payment_card_password_configured
            values["operation"] = "change" if configured else "set"
        return values

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            passwords = {name: vals.pop(name, False) or "" for name in PASSWORD_FIELDS}
            self._apply(vals, passwords)
        return super().create(vals_list)

    @api.model
    def _apply(self, vals, passwords):
        if not self.env.user.has_group("base.group_system"):
            raise UserError(_("Only an administrator can manage the card password."))
        if passwords["new_password"] != passwords["confirm_password"]:
            raise UserError(_("The new password and its confirmation do not match."))
        pms_property = self.env["pms.property"].browse(vals.get("pms_property_id"))
        vaults = self.env["pms.payment.card.vault"]
        vault = vaults._get_for_property(pms_property)
        operation = vals.get("operation")
        if operation == "set":
            vaults._create_for_property(pms_property, passwords["new_password"])
        elif not vault:
            raise UserError(_("This property has no card password yet."))
        elif operation == "change":
            vault._change_password(
                passwords["current_password"], passwords["new_password"]
            )
        elif operation == "reset":
            vault._reset_password(passwords["new_password"])
        else:
            raise UserError(_("Choose what to do with the card password."))
        self.env["pms.payment.card.access.log"]._log(
            OPERATION_LOG_ACTIONS[operation], "success", pms_property=pms_property
        )

    def action_apply(self):
        return {"type": "ir.actions.act_window_close"}
