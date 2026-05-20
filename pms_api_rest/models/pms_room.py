from odoo import fields, models


class PmsRoom(models.Model):
    _name = "pms.room"
    _inherit = ["pms.room", "mail.thread"]

    cleaning_status = fields.Selection(
        selection=[
            ("clean", "Clean"),
            ("dirty", "Dirty"),
            ("reviewed", "Reviewed"),
        ],
        string="Cleaning Status",
        default="clean",
        tracking=True,
    )
