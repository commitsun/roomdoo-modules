# Copyright 2026 Commit [Sun]
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
from odoo import _, api, fields, models
from odoo.exceptions import UserError

PASSWORD_FIELDS = ("password", "confirm_password")


class ChannelWubookBackendCardPasswordWizard(models.TransientModel):
    """Enter the card password of the Wubook account.

    It is sealed with the key pair of the property, so it needs no card
    password to be entered, but only a request with one can use it. Like the
    card password wizard, it acts when saved and never stores the passwords.
    """

    _name = "channel.wubook.backend.card.password.wizard"
    _description = "Wubook card password"

    backend_id = fields.Many2one(
        comodel_name="channel.wubook.backend",
        required=True,
        readonly=True,
    )
    password = fields.Char()
    confirm_password = fields.Char()

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            passwords = {name: vals.pop(name, False) or "" for name in PASSWORD_FIELDS}
            self._apply(vals, passwords)
        return super().create(vals_list)

    @api.model
    def _apply(self, vals, passwords):
        if not self.env.user.has_group("base.group_system"):
            raise UserError(_("Only an administrator can enter this password."))
        if not passwords["password"]:
            raise UserError(_("Enter the card password of the Wubook account."))
        if passwords["password"] != passwords["confirm_password"]:
            raise UserError(_("The password and its confirmation do not match."))
        backend = self.env["channel.wubook.backend"].browse(vals.get("backend_id"))
        vault = self.env["pms.payment.card.vault"]._get_for_property(
            backend.pms_property_id
        )
        if not vault:
            raise UserError(
                _("Create the card password of the property %s first.")
                % backend.pms_property_id.name
            )
        backend.sudo().payment_card_password = vault._seal(
            backend, passwords["password"].encode()
        )

    def action_apply(self):
        return {"type": "ir.actions.act_window_close"}
