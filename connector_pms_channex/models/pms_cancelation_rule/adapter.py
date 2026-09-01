# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo.addons.component.core import Component


class ChannelChannexPmsCancelationRuleAdapter(Component):
    """The collection is not in the API reference -- only in the changelog --
    but it is there, and it behaves like the documented ones: paginated,
    filterable by property, and read one at a time by UUID. The single read is
    the richer one: only it fills ``associated_rate_plan_ids``.
    """

    _name = "channel.channex.pms.cancelation.rule.adapter"
    _inherit = "channel.channex.adapter"
    _apply_on = "channel.channex.pms.cancelation.rule"

    _resource = "cancellation_policies"
    _payload_root = "cancellation_policy"
    _server_filters = ("property_id", "title")
