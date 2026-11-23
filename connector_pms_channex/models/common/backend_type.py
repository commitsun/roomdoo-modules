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

    Only installation-wide vocabularies live here. Anything scoped to a Channex
    account -- the API key, the group -- belongs on the backend, because the
    account is defined by the key.

    Note there is no direct sale channel here, unlike the Wubook backend type:
    Channex has no booking engine of its own, it only connects to OTAs. The OTA
    a reservation came from is a partner, resolved through
    ``channel.channex.ota``, which is installation wide rather than per backend
    type: a channel code means the same partner in every property.
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

    def _is_excluded_class(self, room_type_class):
        """Room type classes flagged as excluded are never sent to Channex."""
        self.ensure_one()
        mapping = self.room_kind_ids.filtered(
            lambda m: m.room_type_class_id == room_type_class
        )
        return bool(mapping[:1].excluded)
