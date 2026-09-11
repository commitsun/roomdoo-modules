# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import fields, models


class ChannelChannexBackendTypeRoomKind(models.Model):
    """Maps an Odoo room type class to the Channex ``room_kind``.

    ``excluded`` replaces the sentinel external id Wubook used to mean "do not
    synchronise": an explicit flag rather than a magic value.
    """

    _name = "channel.channex.backend.type.room.kind"
    _description = "Channel Channex Backend Type Room Kind"

    backend_type_id = fields.Many2one(
        comodel_name="channel.channex.backend.type",
        required=True,
        ondelete="cascade",
    )
    room_type_class_id = fields.Many2one(
        comodel_name="pms.room.type.class",
        string="Room type class",
        required=True,
        ondelete="restrict",
    )
    room_kind = fields.Selection(
        selection=[
            ("room", "Room"),
            ("dorm", "Dorm"),
        ],
        default="room",
        required=True,
    )
    excluded = fields.Boolean(
        help="Room types of this class are never sent to Channex.",
    )

    _sql_constraints = [
        (
            "room_type_class_uniq",
            "unique(backend_type_id, room_type_class_id)",
            "This room type class is already mapped for this backend type.",
        ),
    ]
