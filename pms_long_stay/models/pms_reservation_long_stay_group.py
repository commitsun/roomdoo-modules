from odoo import fields, models


class PmsReservationLongStayGroup(models.Model):
    _name = "pms.reservation.long.stay.group"
    _description = "Long Stay Reservation Group"

    name = fields.Char(
        string="Reference",
        required=True,
        help="Human-readable reference for the long stay group.",
    )

    period = fields.Selection(
        selection=[
            ("weekly", "Weekly"),
            ("monthly", "Monthly"),
        ],
        string="Period",
        help="Base period used for splitting long stay reservations.",
    )

    original_checkin = fields.Datetime(
        string="Original Check-in",
        help="Original check-in date before splitting into periods.",
    )

    original_checkout = fields.Datetime(
        string="Original Check-out",
        help="Original check-out date before splitting into periods.",
    )

    reservation_ids = fields.One2many(
        comodel_name="pms.reservation",
        inverse_name="long_stay_group_id",
        string="Reservations",
    )
