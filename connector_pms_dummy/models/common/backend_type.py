# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import api, fields, models


class ChannelBackendType(models.Model):
    _inherit = "channel.backend.type"

    @api.model
    def _get_channel_backend_type_model_names(self):
        res = super()._get_channel_backend_type_model_names()
        res.append("channel.dummy.backend.type")
        return res


class ChannelDummyBackendType(models.Model):
    _name = "channel.dummy.backend.type"
    _inherits = {"channel.backend.type": "parent_id"}
    _description = "Channel Dummy Backend Type"

    _main_model = "channel.dummy.backend"

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
