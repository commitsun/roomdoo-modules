from odoo import fields, models


class PmsRoom(models.Model):
    _inherit = "pms.room"

    cleaning_status = fields.Selection(
        selection=[
            ("clean", "Clean"),
            ("dirty", "Dirty"),
        ],
        string="Cleaning Status",
        default="clean",
    )
