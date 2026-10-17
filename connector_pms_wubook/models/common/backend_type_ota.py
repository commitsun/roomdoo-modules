# Copyright 2021 Eric Antones <eantones@nuobit.com>
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
import logging

from odoo import fields, models

_logger = logging.getLogger(__name__)


class ChannelWubookBackendTypeOTA(models.Model):
    _name = "channel.wubook.backend.type.ota"
    _description = "Channel Wubook PMS Backend OTA Map"

    backend_type_id = fields.Many2one(
        comodel_name="channel.wubook.backend.type",
        required=True,
        ondelete="cascade",
    )

    wubook_ota = fields.Integer(
        string="Wubook OTA ID",
        required=True,
    )

    import_channel_notes = fields.Boolean(
        string="Channel Notes",
        help="Write in the folio internal notes what the channel passes "
        "through for reception. Only Mirai passes any for now: the loyalty "
        "club membership and the upselling the guest pays at the hotel.",
    )

    agency_id = fields.Many2one(
        comodel_name="res.partner",
        domain="[('is_agency', '=', True)]",
        string="Agency",
    )
