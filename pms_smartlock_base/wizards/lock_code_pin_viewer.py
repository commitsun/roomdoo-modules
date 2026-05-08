from odoo import fields, models


class LockCodePinViewer(models.TransientModel):
    _name = "lock.code.pin.viewer"
    _description = "Lock Code PIN Viewer"

    lock_code_id = fields.Many2one(
        comodel_name="lock.code",
        required=True,
        readonly=True,
    )
    pin = fields.Char(readonly=True)
