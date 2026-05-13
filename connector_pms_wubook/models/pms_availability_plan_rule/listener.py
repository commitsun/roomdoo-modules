# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo.addons.component.core import Component
from odoo.addons.component_event.components.event import skip_if


# Per-transaction buffer for plan re-exports triggered by rule changes.
# Massive operations (typical of calendar wizards that touch hundreds of
# rules at once) collapse to a single job per affected plan binding.
_PLAN_RULES_BUFFER_KEY = "connector_pms_wubook.plan_rules_buffer"


def _flush_plan_rules_buffer(env):
    """Precommit callback: encola un único ``export_record`` por plan
    binding agregado al buffer durante la transacción.
    """
    data = env.cr.precommit.data.pop(_PLAN_RULES_BUFFER_KEY, None)
    if not data:
        return
    for _binding_id, binding in data.items():
        binding = binding.exists()
        if not binding:
            continue
        binding.with_delay().export_record(binding.backend_id, binding.odoo_id)


class ChannelWubookPmsAvailabilityPlanRuleListener(Component):
    """Cascade listener for `pms.availability.plan.rule`.

    A rule does not have its own Wubook entity: it is exported as part of
    the parent plan payload. So every change on a rule schedules a re-export
    of its parent plan — coalesced through a per-transaction buffer so that
    massive operations (e.g. ``wizard_massive_changes`` touching hundreds
    of rules) end up enqueuing **one** job per plan binding.
    """

    _name = "channel.wubook.pms.availability.plan.rule.listener"
    _inherit = "base.connector.listener"
    _apply_on = "pms.availability.plan.rule"

    def _buffer_plan_export(self, plan_binding):
        cr = self.env.cr
        data = cr.precommit.data
        if _PLAN_RULES_BUFFER_KEY not in data:
            data[_PLAN_RULES_BUFFER_KEY] = {}
            env = self.env
            cr.precommit.add(lambda env=env: _flush_plan_rules_buffer(env))
        data[_PLAN_RULES_BUFFER_KEY].setdefault(plan_binding.id, plan_binding)

    def _enqueue_for_rule(self, record):
        plan = record.availability_plan_id
        if not plan:
            return
        for binding in plan.channel_wubook_bind_ids:
            if not binding.external_id:
                # The plan has not been connected yet on this backend.
                continue
            self._buffer_plan_export(binding)

    @skip_if(lambda self, record, **kwargs: self.no_connector_export(record))
    def on_record_create(self, record, fields=None):
        self._enqueue_for_rule(record)

    @skip_if(lambda self, record, **kwargs: self.no_connector_export(record))
    def on_record_write(self, record, fields=None):
        self._enqueue_for_rule(record)

    @skip_if(lambda self, record, **kwargs: self.no_connector_export(record))
    def on_record_unlink(self, record, fields=None):
        # Read the plan now before the rule is gone.
        self._enqueue_for_rule(record)
