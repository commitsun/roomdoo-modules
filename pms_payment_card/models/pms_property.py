# Copyright 2026 Commit [Sun]
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
from odoo import _, fields, models


class PmsProperty(models.Model):
    _inherit = "pms.property"

    payment_card_password_configured = fields.Boolean(
        string="Card password configured",
        compute="_compute_payment_card_password_configured",
    )
    payment_card_retention_days = fields.Integer(
        string="Keep cards added in Roomdoo for",
        default=7,
        help="Days after the check-out after which the cards added in Roomdoo "
        "are deleted.",
    )

    def _compute_payment_card_password_configured(self):
        vaults = self.env["pms.payment.card.vault"]
        for record in self:
            record.payment_card_password_configured = bool(
                vaults._get_for_property(record)
            )

    def action_payment_card_password(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Card password"),
            "res_model": "pms.payment.card.password.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_pms_property_id": self.id},
        }
