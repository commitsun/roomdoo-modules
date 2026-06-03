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
    shared_lock_ids = fields.Many2many(
        comodel_name="pms.common.lock",
        string="Shared Locks",
        domain="[('pms_property_id', '=', pms_property_id)]",
        help="Common doors (entrance, garage…) a guest of this room also gets "
        "access to. The reservation's access grant covers this room's own "
        "lock plus these shared locks, all under the same PIN.",
    )
