# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import fields, models


class ChannelChannexPmsCancelationRule(models.Model):
    """The cancellation policy as Channex knows it.

    Channex scopes a policy to one property -- the collection answers
    ``filter[property_id]`` and answers it truthfully, even though it does not
    echo the property back on the object -- so one rule shared by several hotels
    is one policy per hotel there, which is what a binding per backend already
    expresses.
    """

    _name = "channel.channex.pms.cancelation.rule"
    _inherit = "channel.channex.binding"
    _inherits = {"pms.cancelation.rule": "odoo_id"}
    _description = "Channel Channex PMS Cancelation Rule"

    odoo_id = fields.Many2one(
        comodel_name="pms.cancelation.rule",
        string="Cancelation Rule",
        required=True,
        ondelete="cascade",
    )

    def _channex_property_external_id(self):
        """The UUID of the property this policy belongs to."""
        self.ensure_one()
        binding = self.env["channel.channex.pms.property"].search(
            [
                ("odoo_id", "=", self.backend_id.pms_property_id.id),
                ("backend_id", "=", self.backend_id.id),
            ],
            limit=1,
        )
        return binding.external_id
