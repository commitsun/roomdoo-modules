# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class ChannelWubookBackendBoardService(models.Model):
    """The board services a room type is sold with on Wubook, and their price.

    Wubook knows a board service as a supplement of the room type, with no
    pricelist or date behind it, so it doesn't follow the board services of
    the pricelists nor their prices: they are set here, for each backend.
    """

    _name = "channel.wubook.backend.board.service"
    _description = "Channel Wubook Backend Board Service"
    _order = "room_type_id, board_service_id"

    backend_id = fields.Many2one(
        comodel_name="channel.wubook.backend",
        required=True,
        ondelete="cascade",
    )
    room_type_id = fields.Many2one(
        string="Room Type",
        comodel_name="pms.room.type",
        required=True,
        ondelete="cascade",
    )
    board_service_id = fields.Many2one(
        string="Board Service",
        comodel_name="product.template",
        required=True,
        ondelete="cascade",
        domain=[("is_board_service", "=", True)],
    )
    amount = fields.Float(
        digits="Product Price",
        help="Price of the board service per person and night",
    )
    by_default = fields.Boolean(
        string="Apply by Default",
        help="The board service the price of the room type includes on Wubook",
    )

    _sql_constraints = [
        (
            "board_service_uniq",
            "unique(backend_id, room_type_id, board_service_id)",
            "The board service is already set for this room type",
        ),
    ]

    @api.constrains("backend_id", "room_type_id", "by_default")
    def _check_one_by_default(self):
        for record in self.filtered("by_default"):
            if (
                self.search_count(
                    [
                        ("backend_id", "=", record.backend_id.id),
                        ("room_type_id", "=", record.room_type_id.id),
                        ("by_default", "=", True),
                    ]
                )
                > 1
            ):
                raise ValidationError(
                    _("Room type %s: only one board service can apply by default")
                    % record.room_type_id.name
                )

    def _get_night_price(self, adults, children):
        """What the board service of a room comes to in a night: its amount
        for each of the units it makes the guests consume, as pms counts them
        to charge it"""
        self.ensure_one()
        guests = {"adults": adults, "children": children, "all": adults + children}
        consumed = self.board_service_id._get_board_service_products(adults, children)
        units = sum(
            guests[guest_type] if product.per_person else 1
            for product, guest_type in consumed
        )
        return self.amount * units
