from odoo import fields, models


class ResPartner(models.Model):
    _inherit = "res.partner"

    bookai_exclude_payment_reminders = fields.Boolean(
        string="BookAI Exclude Payment Reminders",
        help=(
            "When enabled for an agency, payment-related BookAI reminders are "
            "excluded for folios linked to that agency."
        ),
    )
