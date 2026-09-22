from odoo import fields, models


class ProductTemplate(models.Model):
    _inherit = "product.template"

    is_long_stay_product = fields.Boolean(
        string="Long Stay Product",
        help="If enabled, this product can be used as a long stay product "
        "for a room type.",
    )
