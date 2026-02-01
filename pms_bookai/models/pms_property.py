from odoo import fields, models


class PmsProperty(models.Model):
    _inherit = "pms.property"

    bookai_mode = fields.Selection(
        [
            ("disabled", "Disabled"),
            ("manual", "Manual"),
            ("ai", "AI"),
        ],
        string="BookAI Mode",
        default="disabled",
        required=True,
        help=(
            "Controls BookAI usage for this property.\n"
            "- Disabled: no BookAI sending.\n"
            "- Manual: BookAI can be used only when triggered manually.\n"
            "- AI: BookAI can be used automatically by rules / flows."
        ),
    )

    external_code = fields.Char(
        string="External Hotel Code",
        help="External hotel code for BookAI integration (e.g., 'EXT_TEST').",
    )

    def get_bookai_hotel_info(self):
        self.ensure_one()
        fallback_code = ""
        if "pms_property_code" in self._fields:
            fallback_code = self.pms_property_code or ""
        return {
            "id": self.id,
            "external_code": self.external_code or fallback_code,
            "name": self.name or "",
            "bookai_mode": self.bookai_mode or "disabled",
        }
