# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

STAGING_URL = "https://staging.channex.io/api/v1"
PRODUCTION_URL = "https://channex.io/api/v1"


class ChannelChannexBackend(models.Model):
    _name = "channel.channex.backend"
    _inherit = "connector.backend"
    _inherits = {"channel.backend": "parent_id"}
    _description = "Channel Channex Backend"
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

    environment = fields.Selection(
        selection=[
            ("staging", "Staging"),
            ("production", "Production"),
        ],
        default="staging",
        required=True,
    )
    url = fields.Char(
        compute="_compute_url",
        store=True,
        readonly=False,
        help="Overridable, but it normally follows the environment.",
    )
    api_key = fields.Char(
        string="API key",
        required=True,
        groups="connector.group_connector_manager",
    )
    timeout_connect = fields.Integer(default=5, required=True)
    timeout_read = fields.Integer(default=30, required=True)
    page_limit = fields.Integer(
        default=100,
        required=True,
        help="Items per page when listing. Channex defaults to 10.",
    )

    @api.depends("environment")
    def _compute_url(self):
        for rec in self:
            rec.url = PRODUCTION_URL if rec.environment == "production" else STAGING_URL

    def action_test_connection(self):
        """Cheapest authenticated call: one page of one property."""
        self.ensure_one()
        with self.work_on("channel.channex.pms.property") as work:
            adapter = work.component(usage="backend.adapter")
            adapter.request("GET", "properties", params={"pagination[limit]": 1})
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "success",
                "message": _("Connected to %s.") % self.url,
                "sticky": False,
            },
        }

    def _channex_group_id(self):
        """Channex requires every property to belong to a group."""
        self.ensure_one()
        group_id = self.backend_type_id.child_id.group_id
        if not group_id:
            raise UserError(
                _(
                    "This backend type has no Channex group yet. Fetch or create "
                    "one before exporting a property."
                )
            )
        return group_id
