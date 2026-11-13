# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class ResPartner(models.Model):
    """The agency carries the price modifier every channel manager applies.

    It lives here, on the partner, and not on a per-connector model because the
    agency is the one thing every connector already resolves its OTA to: Channex
    goes channel -> ota -> agency, Wubook goes wubook_ota -> agency. Kept per
    connector it would have to be typed once per channel manager, which is not
    reuse at all.

    Odoo never applies it: the modifier is exported, the channel manager does
    the arithmetic, and the reservation comes back with the price already
    computed and blocked.
    """

    _inherit = "res.partner"

    ota_price_modifier_type = fields.Selection(
        string="OTA Rate Logic",
        selection=[
            ("increase_amount", "Increase By Amount"),
            ("decrease_amount", "Decrease By Amount"),
            ("increase_percent", "Increase By Percent"),
            ("decrease_percent", "Decrease By Percent"),
        ],
        help="How the price sent to this agency is modified. Only channel "
        "managers that support a per-OTA modifier publish it; Wubook has no "
        "such thing in its API and ignores it.",
    )
    ota_price_modifier_value = fields.Float(
        string="OTA Rate Value",
        help="How much to increase or decrease by. Always positive: the "
        "direction is the rate logic, not the sign.",
    )

    @api.constrains("ota_price_modifier_type", "ota_price_modifier_value")
    def _check_ota_price_modifier(self):
        for partner in self:
            modifier_type = partner.ota_price_modifier_type
            value = partner.ota_price_modifier_value
            if modifier_type and value <= 0:
                raise ValidationError(
                    _("The OTA rate value must be greater than zero.")
                )
            if value and not modifier_type:
                raise ValidationError(
                    _("Choose the rate logic for the OTA rate value.")
                )
            if modifier_type == "decrease_percent" and value >= 100:
                raise ValidationError(
                    _("An OTA rate cannot be decreased by 100% or more.")
                )
