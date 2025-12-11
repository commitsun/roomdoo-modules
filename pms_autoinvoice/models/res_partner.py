from odoo import fields, models


class ResPartner(models.Model):
    _inherit = "res.partner"
    invoicing_policy = fields.Selection(
        help="""The invoicing policy of the partner,
         set Property to user the policy configured in the Property""",
        selection=[
            ("property", "Property Policy Invoice"),
            ("manual", "Manual"),
            ("checkout", "From Checkout"),
            ("month_day", "Month Day Invoice"),
        ],
        default="property",
    )
    invoicing_month_day = fields.Integer(
        help="The day of the month to invoice",
    )
    margin_days_autoinvoice = fields.Integer(
        string="Days from Checkout",
        help="Days from Checkout to generate the invoice",
    )
