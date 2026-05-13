# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo.addons.component.core import Component
from odoo.addons.component_event.components.event import skip_if

# Fields whose change on the plan record warrants a re-export. Rule-level
# changes are coalesced separately by the rule listener, so we don't want
# a wizard writing ``plan.write({"rule_ids": [...]})`` to trigger a
# redundant ``rplan_rename_rplan`` call here.
_PLAN_RELEVANT_FIELDS = {"name"}


class ChannelWubookPmsAvailabilityPlanListener(Component):
    """Auto-push for already-bound availability plans. Rule changes are
    coalesced separately by the rule listener
    (``pms_availability_plan_rule/listener.py``); this one fires only when
    a relevant plan-level field (``name``) is written.
    """

    _name = "channel.wubook.pms.availability.plan.listener"
    _inherit = "base.connector.listener"
    _apply_on = "pms.availability.plan"

    @skip_if(lambda self, record, **kwargs: self.no_connector_export(record))
    def on_record_write(self, record, fields=None):
        if not fields or not (set(fields) & _PLAN_RELEVANT_FIELDS):
            return
        for binding in record.channel_wubook_bind_ids:
            if not binding.external_id:
                continue
            binding.with_delay().export_record(binding.backend_id, record)
