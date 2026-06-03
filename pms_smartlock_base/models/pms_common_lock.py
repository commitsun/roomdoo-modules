from odoo import fields, models


class PmsCommonLock(models.Model):
    """A shared/common door of a property (main entrance, garage, pool…).

    Unlike a room lock, a common lock serves many rooms. It is declared
    once per property and associated to the rooms whose guests should reach
    it through ``pms.room.shared_lock_ids``. This keeps multi-portal
    properties (e.g. tourist flats with several street entrances) working:
    each room points only to the common doors that apply to it.
    """

    _name = "pms.common.lock"
    _description = "Common Area Lock"
    _order = "sequence, name"

    name = fields.Char(required=True)
    pms_property_id = fields.Many2one(
        comodel_name="pms.property",
        string="Property",
        required=True,
        ondelete="cascade",
        index=True,
    )
    vendor_id = fields.Many2one(
        comodel_name="lock.vendor",
        string="Lock Vendor",
        required=True,
        ondelete="restrict",
        domain="[('pms_property_id', '=', pms_property_id)]",
    )
    lock_device_id = fields.Char(
        string="Lock Device ID",
        required=True,
        help="Device identifier used by the vendor API to target this lock.",
    )
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
