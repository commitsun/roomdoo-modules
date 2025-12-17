from odoo import fields, models


class PmsRoom(models.Model):
    _inherit = "pms.room"

    guest_visible_name = fields.Char(
        string="Guest Visible Name",
        help="Guest-facing room name or label, e.g. 'Room 101 – Sea View'.",
        translate=True,
    )
    location_hint = fields.Text(
        string="Location Hint",
        help="Short instructions to help guests locate the room "
        "(e.g. '2nd floor, left wing, near the elevator').",
        translate=True,
    )
    building_label = fields.Char(
        string="Building Label",
        help="Guest-facing building label when the property has multiple buildings.",
        translate=True,
    )
    notification_internal_note = fields.Text(
        string="Notification Internal Note",
        help="Internal note related to how this specific room should be mentioned "
        "in guest communications.",
        translate=True,
    )
