# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import api, fields, models


class ChannelChannexPmsRoomType(models.Model):
    """The room type as Channex knows it.

    Odoo does not model capacity on ``pms.room.type``: it lives on each
    ``pms.room``. The occupancies are therefore derived from the rooms of this
    type **in the property of this backend**, and stay editable, because a room
    type whose rooms differ in capacity has no single correct answer.
    """

    _name = "channel.channex.pms.room.type"
    _inherit = "channel.channex.binding"
    _inherits = {"pms.room.type": "odoo_id"}
    _description = "Channel Channex PMS Room Type"

    odoo_id = fields.Many2one(
        comodel_name="pms.room.type",
        string="Room Type",
        required=True,
        ondelete="cascade",
    )

    count_of_rooms = fields.Integer(
        compute="_compute_from_rooms",
        store=True,
        readonly=False,
    )
    occ_adults = fields.Integer(
        string="Adults",
        compute="_compute_from_rooms",
        store=True,
        readonly=False,
        help="Defaults to the smallest capacity among the rooms of this type.",
    )
    default_occupancy = fields.Integer(
        compute="_compute_from_rooms",
        store=True,
        readonly=False,
    )
    occ_children = fields.Integer(string="Children")
    occ_infants = fields.Integer(string="Infants")
    room_kind = fields.Selection(
        selection=[
            ("room", "Room"),
            ("dorm", "Dorm"),
        ],
        compute="_compute_room_kind",
        store=True,
        readonly=False,
    )
    capacity = fields.Integer(help="Only meaningful for a dorm.")
    heterogeneous_capacity = fields.Boolean(
        compute="_compute_from_rooms",
        store=True,
        help="The rooms of this type do not all hold the same number of guests, "
        "so the occupancy sent to Channex is a choice: the smallest capacity is "
        "used, which never oversells but may hide beds.",
    )

    def _backend_rooms(self):
        """Rooms of this type in the property this backend serves."""
        self.ensure_one()
        return self.env["pms.room"].search(
            [
                ("room_type_id", "=", self.odoo_id.id),
                ("pms_property_id", "=", self.backend_id.pms_property_id.id),
            ]
        )

    @api.depends(
        "odoo_id",
        "backend_id",
        "backend_id.pms_property_id",
        "odoo_id.room_ids",
        "odoo_id.room_ids.active",
        "odoo_id.room_ids.capacity",
        "odoo_id.room_ids.pms_property_id",
    )
    def _compute_from_rooms(self):
        for rec in self:
            rooms = rec._backend_rooms()
            capacities = [c for c in rooms.mapped("capacity") if c]
            rec.count_of_rooms = len(rooms)
            rec.heterogeneous_capacity = bool(capacities) and (
                min(capacities) != max(capacities)
            )
            # min, not max: overselling a bed that a given room does not have
            # causes an overbooking, while underselling only loses inventory.
            rec.occ_adults = min(capacities) if capacities else 0
            rec.default_occupancy = rec.occ_adults

    @api.depends("odoo_id", "backend_id")
    def _compute_room_kind(self):
        for rec in self:
            mapping = rec.backend_id.backend_type_id.child_id.room_kind_ids.filtered(
                lambda m, rec=rec: m.room_type_class_id == rec.odoo_id.class_id
            )
            rec.room_kind = mapping[:1].room_kind or "room"

    def _channex_property_external_id(self):
        """The UUID of the property this room type is exported under.

        Every Channex room type belongs to a property, so the property has to be
        exported first; the exporter declares that dependency.
        """
        self.ensure_one()
        binding = self.env["channel.channex.pms.property"].search(
            [
                ("odoo_id", "=", self.backend_id.pms_property_id.id),
                ("backend_id", "=", self.backend_id.id),
            ],
            limit=1,
        )
        return binding.external_id

    def _is_excluded(self):
        """Room type classes flagged as excluded are never sent."""
        self.ensure_one()
        return self.backend_id.backend_type_id.child_id._is_excluded_class(
            self.odoo_id.class_id
        )
