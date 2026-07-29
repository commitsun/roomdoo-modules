# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class ChannelBackendType(models.Model):
    _inherit = "channel.backend.type"

    @api.model
    def _get_channel_backend_type_model_names(self):
        res = super()._get_channel_backend_type_model_names()
        res.append("channel.channex.backend.type")
        return res


class ChannelChannexBackendType(models.Model):
    """Business mapping shared by every backend of this channel manager.

    Note there is no direct sale channel here, unlike the Wubook backend type:
    Channex has no booking engine of its own, it only connects to OTAs. The OTA
    a reservation came from is a partner, resolved through ``ota_ids``.
    """

    _name = "channel.channex.backend.type"
    _inherits = {"channel.backend.type": "parent_id"}
    _description = "Channel Channex Backend Type"

    _main_model = "channel.channex.backend"

    parent_id = fields.Many2one(
        comodel_name="channel.backend.type",
        string="Parent Channel Backend Type",
        required=True,
        ondelete="cascade",
    )

    _sql_constraints = [
        (
            "backend_parent_uniq",
            "unique(parent_id)",
            "Only one backend child is allowed for each generic backend.",
        ),
    ]

    group_id = fields.Char(
        string="Channex group ID",
        help="Channex requires every property to belong to a group.",
    )
    group_title = fields.Char(string="Channex group name", readonly=True)
    default_property_type = fields.Selection(
        selection=[
            ("hotel", "Hotel"),
            ("apartment", "Apartment"),
            ("hostel", "Hostel"),
            ("guest_house", "Guest house"),
        ],
        default="hotel",
        required=True,
    )
    room_kind_ids = fields.One2many(
        comodel_name="channel.channex.backend.type.room.kind",
        inverse_name="backend_type_id",
        string="Room type classes",
    )
    ota_ids = fields.One2many(
        comodel_name="channel.channex.backend.type.ota",
        inverse_name="backend_type_id",
        string="OTAs",
    )
