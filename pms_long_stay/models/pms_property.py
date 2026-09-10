from odoo import fields, models


class PmsProperty(models.Model):
    _inherit = "pms.property"

    week_start_day = fields.Selection(
        [
            ("monday", "Monday"),
            ("sunday", "Sunday"),
            ("saturday", "Saturday"),
        ],
        string="Week Start Day",
        default="monday",
        help="Defines the first day of the week for long-stay splitting.",
    )
    long_stay_billing_timing = fields.Selection(
        selection=[
            ("start", "Invoice at period start"),
            ("end", "Invoice at period end"),
        ],
        string="Long Stay Billing Timing",
        default="end",
        help=(
            "Defines whether long stay periods are invoiced at the beginning "
            "or at the end of each period. This controls the date of the "
            "generated long stay service lines."
        ),
    )
