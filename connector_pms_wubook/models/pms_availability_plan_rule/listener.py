# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo.addons.component.core import Component
from odoo.addons.component_event.components.event import skip_if

# Per-transaction buffer for plan re-exports triggered by rule changes.
# Massive operations (typical of calendar wizards that touch hundreds of
# rules at once) collapse to a single job per affected plan binding.
_PLAN_RULES_BUFFER_KEY = "connector_pms_wubook.plan_rules_buffer"

# Sentinel key for the per-plan staging buffer populated synchronously by
# the listener on every write/create/unlink. Resolved **once** at
# precommit by ``_flush_pending_plan_rules``, which then iterates the
# plan's Wubook bindings a single time per (plan, property) pair and
# forwards to ``_PLAN_RULES_BUFFER_KEY`` (the per-binding deduplicating
# buffer).
#
# Why the indirection: ``plan.channel_wubook_bind_ids`` resolves to ~one
# binding per Wubook backend. Iterating it per rule during a multi-rule
# write (e.g. calendar wizard, mass restrictions update) is the bulk of
# the listener cost. Doing it once per (plan, property) at precommit
# collapses that to a constant.
_PENDING_PLAN_RULES_KEY = "connector_pms_wubook.pending_plan_rules"


# Rule fields whose change must re-push the parent plan (rules payload):
# stay restrictions / closures / OTA opt-out. ``real_avail`` and ``avail_id``
# are intentionally NOT here — those are technical recomputes that flow from
# reservation line changes.
_RULE_PLAN_FIELDS = frozenset(
    {
        "min_stay",
        "min_stay_arrival",
        "max_stay",
        "max_stay_arrival",
        "closed",
        "closed_arrival",
        "closed_departure",
        "no_ota",
    }
)


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
        # Coarse identity_key per plan binding so bursts of rule changes
        # spanning several transactions collapse to at most one PENDING
        # job per plan in queue_job (eventual consistency, no flood).
        binding.with_delay(
            identity_key=f"wubook_export_record:{binding._name}:{binding.id}"
        ).export_record(binding.backend_id, binding.odoo_id)


def _flush_pending_plan_rules(env):
    """Precommit callback: resolve plan bindings **once per (plan,
    property) pair** and forward to the per-binding plan-rules buffer.

    Behavior is equivalent to calling ``_enqueue_for_rule`` rule-by-rule:
    the per-binding buffer collapses N rule-level requests into one job,
    so a single (plan, property) pass yields the same set of jobs.
    """
    data = env.cr.precommit.data.pop(_PENDING_PLAN_RULES_KEY, None)
    if not data:
        return
    Plan = env["pms.availability.plan"]
    for plan_id, property_ids in data.items():
        plan = Plan.browse(plan_id).exists()
        if not plan:
            continue
        for binding in plan.channel_wubook_bind_ids:
            if not binding.external_id:
                # The plan has not been connected yet on this backend.
                continue
            # The plan can be global (shared by N properties) and own
            # one binding per backend. A rule belongs to a single
            # property, so only the binding whose backend covers an
            # affected property needs the push.
            if binding.backend_id.pms_property_id.id not in property_ids:
                continue
            _buffer_plan_export_at(env, binding)


def _buffer_plan_export_at(env, plan_binding):
    """Module-level twin of ``_buffer_plan_export``, callable from the
    precommit flush (no ``self``).
    """
    cr = env.cr
    data = cr.precommit.data
    if _PLAN_RULES_BUFFER_KEY not in data:
        data[_PLAN_RULES_BUFFER_KEY] = {}
        env_captured = env
        cr.precommit.add(lambda env=env_captured: _flush_plan_rules_buffer(env))
    data[_PLAN_RULES_BUFFER_KEY].setdefault(plan_binding.id, plan_binding)


class ChannelWubookPmsAvailabilityPlanRuleListener(Component):
    """Cascade listener for ``pms.availability.plan.rule``.

    A rule does not have its own Wubook entity: it is exported as part
    of the parent plan payload, so a change on a rule schedules a
    re-export of its parent **plan**.

    A rule no longer moves the property availability: the commercial
    inventory lives in ``pms.inventory.rule`` and its own listener
    schedules that push.

    The path coalesces through a per-transaction buffer so that massive
    operations (e.g. the ``pms.massive.changes.wizard`` touching hundreds
    of rules) end up enqueuing **one** job per plan binding.

    Performance: on write/create/unlink, the listener appends to a
    cheap per-(plan|property) staging buffer in ``cr.precommit.data``
    and lets a single precommit callback walk
    ``plan.channel_wubook_bind_ids`` / ``prop.channel_wubook_bind_ids``
    once per (plan, property) pair (instead of once per rule).
    """

    _name = "channel.wubook.pms.availability.plan.rule.listener"
    _inherit = "base.connector.listener"
    _apply_on = "pms.availability.plan.rule"

    # --- staging helpers --------------------------------------------

    def _stage_plan_rule(self, record):
        """Append a (plan_id, property_id) fingerprint to the staging
        buffer. Cheap (dict / set). Binding resolution happens later,
        once per (plan, property), at precommit.
        """
        plan = record.availability_plan_id
        if not plan:
            return
        rule_property_id = record.pms_property_id.id
        if not rule_property_id:
            return
        cr = self.env.cr
        data = cr.precommit.data
        if _PENDING_PLAN_RULES_KEY not in data:
            data[_PENDING_PLAN_RULES_KEY] = {}
            env = self.env
            cr.precommit.add(lambda env=env: _flush_pending_plan_rules(env))
        data[_PENDING_PLAN_RULES_KEY].setdefault(plan.id, set()).add(rule_property_id)

    # --- event handlers ---------------------------------------------

    @skip_if(lambda self, record, **kwargs: self.no_connector_export(record))
    def on_record_create(self, record, fields=None):
        self._stage_plan_rule(record)

    @skip_if(lambda self, record, **kwargs: self.no_connector_export(record))
    def on_record_write(self, record, fields=None):
        if not fields:
            return
        changed = set(fields)
        if changed & _RULE_PLAN_FIELDS:
            self._stage_plan_rule(record)

    @skip_if(lambda self, record, **kwargs: self.no_connector_export(record))
    def on_record_unlink(self, record, fields=None):
        # Read the plan / property before the rule is gone.
        self._stage_plan_rule(record)
