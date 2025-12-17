from odoo import fields, models


class ProductPricelist(models.Model):
    _inherit = "product.pricelist"

    guest_rate_name = fields.Char(
        string="Guest Rate Name",
        help="Rate name shown to guests, e.g. 'Flexible Rate with Breakfast'.",
        translate=True,
    )
    guest_rate_description = fields.Text(
        string="Guest Rate Description",
        help="Short guest-facing description of what the rate includes and its"
        "main conditions.",
        translate=True,
    )
    payment_terms_text = fields.Text(
        string="Payment Terms Text",
        help="""Guest-facing explanation of
        payment terms (deposits, charge timings, etc.).
        """,
        translate=True,
    )
    cancellation_terms_text_override = fields.Text(
        string="Cancellation Terms Text Override",
        help="""Optional guest-facing cancellation text
        used instead of the default cancellation rule
        text when this rate has special conditions.
        """,
        translate=True,
    )
    notification_internal_note = fields.Text(
        string="Notification Internal Note",
        help="""Internal notes about how this rate
        should be described in communications.
        """,
        translate=True,
    )
