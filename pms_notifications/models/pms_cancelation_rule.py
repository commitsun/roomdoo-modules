from odoo import fields, models


class PmsCancelationRule(models.Model):
    _inherit = "pms.cancelation.rule"

    guest_policy_name = fields.Char(
        string="Guest Policy Name",
        help="Guest-facing label for this cancellation policy, "
        "e.g. 'Flexible 24h', 'Non-refundable'.",
        translate=True,
    )
    short_policy_text = fields.Text(
        string="Short Policy Text",
        help="Compact guest-facing summary of the cancellation policy.",
        translate=True,
    )
    full_policy_text = fields.Text(
        string="Full Policy Text",
        help="Full guest-facing description of the cancellation policy.",
        translate=True,
    )
    no_show_policy_text = fields.Text(
        string="No-show Policy Text",
        help="Guest-facing text describing the no-show policy, "
        "if different from the standard cancellation terms.",
        translate=True,
    )
    refund_timing_text = fields.Text(
        string="Refund Timing Text",
        help="Explanation of when refunds are processed (e.g. 'Refund will be "
        "processed within 7 working days').",
        translate=True,
    )
    notification_internal_note = fields.Text(
        string="Notification Internal Note",
        help="Internal remarks on how to explain this policy to guests.",
        translate=True,
    )
