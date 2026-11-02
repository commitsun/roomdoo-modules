# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class PmsProperty(models.Model):
    _inherit = "pms.property"

    booking_engine_sale_channel_id = fields.Many2one(
        string="Booking Engine Sale Channel",
        help="Sale channel of the web booking engine. When set, the property is "
        "bookable online with the pricelists of this channel available for it.",
        comodel_name="pms.sale.channel",
        domain="[('channel_type', '=', 'direct'), ('is_on_line', '=', True)]",
        ondelete="restrict",
    )
    booking_engine_show_recommended = fields.Boolean(
        string="Recommend Rooms in the Booking Engine",
        help="Suggest the combination of rooms that best fits the guests searched.",
        default=True,
    )
    booking_engine_hold_minutes = fields.Integer(
        string="Booking Engine Hold (minutes)",
        help="Minutes the rooms of a booking stay held while the guest pays at the "
        "gateway. The booking is released if the payment does not arrive by then.",
        default=15,
    )
    booking_engine_allowed_origins = fields.Text(
        string="Booking Engine Allowed Origins",
        help="Origins of the websites the guest can come back to from the payment "
        "gateway, one per line, e.g. https://www.example.com",
    )

    @api.constrains("booking_engine_hold_minutes")
    def _check_booking_engine_hold_minutes(self):
        for record in self:
            if record.booking_engine_hold_minutes < 1:
                raise ValidationError(
                    _("The booking engine hold must last at least one minute.")
                )

    def _get_booking_engine_allowed_origins(self):
        self.ensure_one()
        return {
            origin.strip().rstrip("/").lower()
            for origin in (self.booking_engine_allowed_origins or "").splitlines()
            if origin.strip()
        }

    @api.constrains("booking_engine_sale_channel_id")
    def _check_booking_engine_sale_channel(self):
        for record in self:
            channel = record.booking_engine_sale_channel_id
            if channel.pms_property_ids and record not in channel.pms_property_ids:
                raise ValidationError(
                    _("The sale channel %(channel)s is not available for %(property)s.")
                    % {"channel": channel.name, "property": record.name}
                )
