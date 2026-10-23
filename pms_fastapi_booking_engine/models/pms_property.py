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

    @api.constrains("booking_engine_sale_channel_id")
    def _check_booking_engine_sale_channel(self):
        for record in self:
            channel = record.booking_engine_sale_channel_id
            if channel.pms_property_ids and record not in channel.pms_property_ids:
                raise ValidationError(
                    _("The sale channel %(channel)s is not available for %(property)s.")
                    % {"channel": channel.name, "property": record.name}
                )
