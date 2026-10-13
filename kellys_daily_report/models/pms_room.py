from odoo import fields, models


class PmsRoom(models.Model):
    _inherit = "pms.room"

    cleaning_sequence = fields.Integer(
        string="Cleaning Order",
        default=0,
        help="Order of the room in the cleaning report; rooms with lower "
        "values are listed first. It is independent of the display order "
        "(sequence) used in the planning. Rooms with the same value are "
        "listed by name.",
    )
