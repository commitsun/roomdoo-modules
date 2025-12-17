from odoo import fields, models


class PmsProperty(models.Model):
    _inherit = "pms.property"

    arrival_instructions = fields.Text(
        string="Arrival Instructions",
        help="Arrival instructions that will be used in guest communications.",
        translate=True,
    )
    welcome_message = fields.Text(
        string="Welcome Message",
        help="Generic welcome message that can be reused across multiple templates.",
        translate=True,
    )
    parking_info = fields.Text(
        string="Parking Information",
        help="Information about the property's parking facilities.",
        translate=True,
    )
    checkin_time_info = fields.Char(
        string="Check-in Time Info",
        help="Information about check-in time (e.g. 'From 15:00').",
        translate=True,
    )
    checkout_time_info = fields.Char(
        string="Check-out Time Info",
        help="Information about check-out time.",
        translate=True,
    )
    digital_checkin_help = fields.Text(
        string="Digital Check-in Help",
        help="Explanation about how to use digital check-in.",
        translate=True,
    )
    prearrival_extra_info = fields.Text(
        string="Pre-arrival Extra Info",
        help="Additional information useful before arrival (transport, access, etc.).",
        translate=True,
    )
    critical_contact_phone = fields.Char(
        string="Critical Contact Phone",
        help="Contact phone number for critical incidents.",
        translate=True,
    )
    default_email_signature = fields.Text(
        string="Default Email Signature",
        help="Default signature for PMS-generated emails.",
        translate=True,
    )
