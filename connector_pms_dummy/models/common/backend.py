# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import fields, models


class ChannelDummyBackend(models.Model):
    """Same parent/child shape every connector follows: the vendor backend
    delegates to the generic ``channel.backend`` through ``parent_id``, which is
    what lets several channel managers share one property."""

    _name = "channel.dummy.backend"
    _inherit = "connector.backend"
    _inherits = {"channel.backend": "parent_id"}
    _description = "Channel Dummy Backend"
    _check_pms_properties_auto = True

    parent_id = fields.Many2one(
        comodel_name="channel.backend",
        string="Parent Channel Backend",
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
