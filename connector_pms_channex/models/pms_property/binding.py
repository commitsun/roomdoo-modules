# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import fields, models


class ChannelChannexPmsProperty(models.Model):
    """The property as Channex knows it.

    Channex asks for settings Odoo has no concept of, so they live on the
    binding, per backend, with sensible defaults and editable by the user. None
    of them belongs on ``pms.property``: they are this channel manager's
    concern, not the hotel's.
    """

    _name = "channel.channex.pms.property"
    _inherit = "channel.channex.binding"
    _inherits = {"pms.property": "odoo_id"}
    _description = "Channel Channex PMS Property"

    odoo_id = fields.Many2one(
        comodel_name="pms.property",
        string="Property",
        required=True,
        ondelete="cascade",
    )

    channex_property_type = fields.Selection(
        selection=[
            ("hotel", "Hotel"),
            ("apartment", "Apartment"),
            ("hostel", "Hostel"),
            ("guest_house", "Guest house"),
        ],
        string="Property type",
        default="hotel",
        required=True,
    )
    # Letting Channex decrement on confirmation narrows the overbooking window,
    # and because availability is pushed as an absolute number there is no risk
    # of double counting: the next push from Odoo corrects it either way.
    # The other two are off on purpose, or Channex could inflate inventory that
    # Odoo has not released yet.
    allow_avail_autoupdate_on_confirmation = fields.Boolean(
        string="Auto-update availability on confirmation",
        default=True,
    )
    allow_avail_autoupdate_on_modification = fields.Boolean(
        string="Auto-update availability on modification",
    )
    allow_avail_autoupdate_on_cancellation = fields.Boolean(
        string="Auto-update availability on cancellation",
    )
    min_stay_type = fields.Selection(
        selection=[
            ("both", "Arrival and through"),
            ("arrival", "Arrival only"),
        ],
        default="both",
        required=True,
        help="'both' is required for arrival and through restrictions to coexist.",
    )
    state_length = fields.Integer(
        string="Days published",
        default=500,
        required=True,
        help="Must be at least as long as the window pushed later on.",
    )
    cut_off_time = fields.Char(default="00:00:00")
    cut_off_days = fields.Integer()

    def _channex_default_cancellation_policy_id(self):
        """The UUID of the policy the OTAs should show for this hotel.

        Which is the rule of the hotel's default pricelist, exported as a policy
        of its own. Empty until that policy exists, which is why the rule
        reconciliation exports the policy first and the property second.
        """
        self.ensure_one()
        rule = self.odoo_id.default_pricelist_id.cancelation_rule_id
        if not rule:
            return False
        binding = self.env["channel.channex.pms.cancelation.rule"].search(
            [("odoo_id", "=", rule.id), ("backend_id", "=", self.backend_id.id)],
            limit=1,
        )
        return binding.external_id
