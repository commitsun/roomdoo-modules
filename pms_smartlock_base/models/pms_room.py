from odoo import fields, models


class PmsRoom(models.Model):
    _inherit = "pms.room"

    lock_vendor_id = fields.Many2one(
        comodel_name="lock.vendor",
        string="Lock Vendor",
        ondelete="restrict",
        domain="[('pms_property_id', '=', pms_property_id)]",
    )
    lock_device_id = fields.Char(
        string="Lock Device ID",
        help="Device identifier used by the vendor API to target this room's lock.",
    )
