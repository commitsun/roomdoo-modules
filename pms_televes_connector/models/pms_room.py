# Copyright 2024 Commit [Sun]
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import fields, models


class PmsRoom(models.Model):
    _inherit = "pms.room"

    televes_enabled = fields.Boolean(
        string="Televes Enabled",
        related="pms_property_id.televes_enabled",
        store=False,
    )
    televes_room_number = fields.Integer(
        string="Televes Room Number",
        help=(
            "Room number used in the Televes/Arantia ATV3 IPTV system. "
            "Must match the room number configured in ATV3."
        ),
        default=0,
    )
