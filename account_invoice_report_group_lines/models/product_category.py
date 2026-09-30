from odoo import fields, models


class ProductCategory(models.Model):
    _inherit = "product.category"

    invoice_report_group_mode = fields.Selection(
        selection=[
            ("detail", "One line per invoice line"),
            ("collapse", "A single line with a breakdown"),
        ],
        string="Invoice report grouping",
        default="detail",
        required=True,
        help="How invoice lines of products in this category are rendered by "
        "reports that support grouping. 'One line per invoice line' is the "
        "standard behaviour. 'A single line with a breakdown' merges them "
        "into one line showing the total, with the amount per product "
        "underneath: useful for many small recurring charges such as bar or "
        "vending consumption.",
    )
    invoice_report_sequence = fields.Integer(
        string="Invoice report order",
        default=10,
        help="Order of this category's group in the invoice report. Lower "
        "comes first. Lines whose category shares the same order keep the "
        "order they have in the invoice.",
    )
    invoice_report_label = fields.Char(
        string="Invoice report label",
        translate=True,
        help="Heading shown for this group in the invoice report. Empty means "
        "the category name is used.",
    )
