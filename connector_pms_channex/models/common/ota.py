# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import api, fields, models


class ChannelChannexOta(models.Model):
    """The agency behind a Channex channel code.

    Installation wide on purpose: Booking.com is the same partner in every
    property, so the mapping is done once. Wubook's equivalent table hangs off
    the backend type and has to be filled in again for every one of them.

    No sale channel is created for this connector: the OTA is a partner and the
    sale channel is the commercial category it already belongs to.
    """

    _name = "channel.channex.ota"
    _description = "Channel Channex OTA"
    _rec_name = "code"
    _order = "code"

    code = fields.Char(
        required=True,
        readonly=True,
        help="The channel code Channex reports, e.g. BookingCom. Rows appear as "
        "channels are discovered; Channex publishes no reliable list of them.",
    )
    agency_id = fields.Many2one(
        comodel_name="res.partner",
        string="Agency",
        domain=[("is_agency", "=", True)],
        ondelete="restrict",
        help="Bookings from any channel with this code are attributed to this "
        "partner, in every property.",
    )
    channel_ids = fields.One2many(
        comodel_name="channel.channex.channel",
        inverse_name="ota_id",
        string="Channels",
    )

    _sql_constraints = [
        (
            "code_uniq",
            "unique(code)",
            "This Channex channel code is already mapped.",
        ),
    ]

    @api.model
    def _channex_of_code(self, code):
        """The row for a code, created if this is the first time it turns up.

        The row, not the partner: which partner an OTA is is a business
        decision, and a guessed one is worse than a missing one.
        """
        ota = self.search([("code", "=", code)], limit=1)
        return ota or self.create({"code": code})
