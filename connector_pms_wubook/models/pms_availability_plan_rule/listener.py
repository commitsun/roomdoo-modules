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


# Rule fields whose change must re-push the parent plan. The dates are in
# here because moving a range moves the nights the restrictions land on,
# which is what Wubook is told.
_RULE_PLAN_FIELDS = frozenset(
    {
        "availability_plan_id",
        "room_type_id",
        "pms_property_id",
        "date_from",
        "date_to",
        "min_stay",
        "min_stay_arrival",
        "max_stay",
        "max_stay_arrival",
        "closed",
        "closed_arrival",
        "closed_departure",
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
    for plan_id, windows in data.items():
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
            window = windows.get(binding.backend_id.pms_property_id.id)
            if not window:
                continue
            binding._wubook_stage_pending_window(*window)
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

    A rule covers a range of nights, and Wubook is written per night, so
    what travels with the request is the window to push. The listener
    accumulates the union of the windows touched during the transaction
    and hands it to the plan binding, which is what the export mapper
    expands back into one value per night.

    Known limit: shortening or moving a range only stages where it is
    now, not the nights it vacated, so those keep whatever Wubook was
    last told until something else touches them. Deleting a rule does
    stage its window, because the record is still readable at unlink.

    The path coalesces through a per-transaction buffer so that massive
    operations (e.g. the ``pms.massive.changes.wizard`` touching hundreds
    of rules) end up enqueuing **one** job per plan binding.

    Performance: on write/create/unlink, the listener appends to a
    cheap per-(plan|property) staging buffer in ``cr.precommit.data``
    and lets a single precommit callback walk
    ``plan.channel_wubook_bind_ids`` once per (plan, property) pair
    (instead of once per rule).
    """

    _name = "channel.wubook.pms.availability.plan.rule.listener"
    _inherit = "base.connector.listener"
    _apply_on = "pms.availability.plan.rule"

    # --- staging helpers --------------------------------------------

    def _stage_plan_rule(self, record):
        """Append the rule's window to the staging buffer, keyed by
        (plan, property). Cheap (dicts). Binding resolution happens
        later, once per (plan, property), at precommit.
        """
        plan = record.availability_plan_id
        if not plan:
            return
        rule_property_id = record.pms_property_id.id
        if not rule_property_id or not record.date_from or not record.date_to:
            return
        cr = self.env.cr
        data = cr.precommit.data
        if _PENDING_PLAN_RULES_KEY not in data:
            data[_PENDING_PLAN_RULES_KEY] = {}
            env = self.env
            cr.precommit.add(lambda env=env: _flush_pending_plan_rules(env))
        windows = data[_PENDING_PLAN_RULES_KEY].setdefault(plan.id, {})
        window = windows.get(rule_property_id)
        if window:
            windows[rule_property_id] = (
                min(window[0], record.date_from),
                max(window[1], record.date_to),
            )
        else:
            windows[rule_property_id] = (record.date_from, record.date_to)

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
        # Read the plan / property / window before the rule is gone.
        self._stage_plan_rule(record)
