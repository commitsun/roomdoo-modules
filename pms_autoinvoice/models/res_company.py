from odoo import fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    pms_invoice_downpayment_policy = fields.Selection(
        selection=[
            ("no", "Manual"),
            ("all", "All"),
            ("checkout_past_month", "Checkout past month"),
        ],
        string="Downpayment policy invoce",
        help="""
            - Manual: Downpayment invoice will be created manually
            - All: Downpayment invoice will be created automatically
            - Current Month: Downpayment invoice will be created automatically
                only for reservations with checkout date past of current month
            """,
        default="no",
    )
