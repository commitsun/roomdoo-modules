from odoo import fields, models


class LockCodeTarget(models.Model):
    """Snapshot of one physical lock covered by a credential's access grant.

    Purely informative: it records *which* locks the grant was requested for
    (room door + shared common doors) so the hotel can see "this code opens
    Room 101 + Main entrance". The per-lock vendor lifecycle is handled inside
    the connector and represented by the credential's opaque
    ``lock.code.vendor_grant_ref`` — there is no per-target vendor state here.
    """

    _name = "lock.code.target"
    _description = "Lock Code Target"
    _order = "kind, id"

    lock_code_id = fields.Many2one(
        comodel_name="lock.code",
        string="Lock Code",
        required=True,
        ondelete="cascade",
        index=True,
    )
    kind = fields.Selection(
        selection=[("room", "Room"), ("common", "Common")],
        required=True,
    )
    lock_device_id = fields.Char(required=True)
    room_id = fields.Many2one(
        comodel_name="pms.room",
        string="Room",
        ondelete="cascade",
        help="Set when this target is the guest's room lock.",
    )
    common_lock_id = fields.Many2one(
        comodel_name="pms.common.lock",
        string="Common Lock",
        ondelete="cascade",
        help="Set when this target is a shared/common door.",
    )

    def name_get(self):
        result = []
        for rec in self:
            label = (
                rec.room_id.display_name
                or rec.common_lock_id.name
                or rec.lock_device_id
            )
            result.append((rec.id, label or "?"))
        return result
