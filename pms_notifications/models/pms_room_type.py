from odoo import fields, models


class PmsRoomType(models.Model):
    _inherit = "pms.room.type"

    guest_display_name = fields.Char(
        string="Guest Display Name",
        help="Human-friendly name shown to guests, e.g. 'Double Room with Sea View'.",
        translate=True,
    )
    guest_short_description = fields.Text(
        string="Guest Short Description",
        help="Short description suitable for emails or messaging (1–2 sentences).",
        translate=True,
    )
    guest_long_description = fields.Text(
        string="Guest Long Description",
        help="Longer description that can be used in richer email templates.",
        translate=True,
    )
    bed_configuration_text = fields.Char(
        string="Bed Configuration Text",
        help="Text description of the bed setup, e.g. '1 double bed', '2 twin beds'.",
        translate=True,
    )
    view_description = fields.Char(
        string="View Description",
        help="Description of the room view, e.g. 'Sea view', 'City view'.",
        translate=True,
    )
    amenities_summary = fields.Text(
        string="Amenities Summary",
        help="Compact summary of key amenities to show in guest communications.",
        translate=True,
    )
    notification_internal_note = fields.Text(
        string="Notification Internal Note",
        help="Internal note for staff about how this room type should be communicated.",
        translate=True,
    )
