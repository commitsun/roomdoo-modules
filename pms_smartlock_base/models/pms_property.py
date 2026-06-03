from odoo import fields, models


class PmsProperty(models.Model):
    _inherit = "pms.property"

    common_lock_ids = fields.One2many(
        comodel_name="pms.common.lock",
        inverse_name="pms_property_id",
        string="Common Locks",
        help="Shared doors of this property (entrance, garage, pool…) that "
        "rooms can grant their guests through their shared locks.",
    )
