# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import fields, models


class ChannelChannexBackendTypeOta(models.Model):
    """Maps the ``ota_name`` Channex reports to the agency partner in Odoo.

    No sale channel is created for this connector: the OTA is a partner and the
    sale channel is the commercial category it already belongs to.
    """

    _name = "channel.channex.backend.type.ota"
    _description = "Channel Channex Backend Type OTA"

    backend_type_id = fields.Many2one(
        comodel_name="channel.channex.backend.type",
        required=True,
        ondelete="cascade",
    )
    ota_name = fields.Char(
        string="Channex OTA name",
        required=True,
        help="Exactly as Channex reports it, e.g. BookingCom.",
    )
    agency_id = fields.Many2one(
        comodel_name="res.partner",
        string="Agency",
        domain=[("is_agency", "=", True)],
        ondelete="restrict",
    )
    channel_external_id = fields.Char(
        string="Channex channel ID",
        readonly=True,
        help="Set when the channel is discovered from Channex.",
    )

    _sql_constraints = [
        (
            "ota_name_uniq",
            "unique(backend_type_id, ota_name)",
            "This OTA is already mapped for this backend type.",
        ),
    ]
