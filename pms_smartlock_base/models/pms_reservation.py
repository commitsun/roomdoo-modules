from odoo import fields, models


class PmsReservation(models.Model):
    _inherit = "pms.reservation"

    lock_code_ids = fields.One2many(
        comodel_name="lock.code",
        inverse_name="reservation_id",
        string="Lock Codes",
    )
