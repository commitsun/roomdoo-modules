# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo.addons.component.core import Component
from odoo.addons.component_event.components.event import skip_if

from ..pms_availability.listener import (
    _AVAILABILITY_BUFFER_KEY,
    _flush_availability_buffer,
)

# Per-transaction buffer for plan re-exports triggered by rule changes.
# Massive operations (typical of calendar wizards that touch hundreds of
# rules at once) collapse to a single job per affected plan binding.
_PLAN_RULES_BUFFER_KEY = "connector_pms_wubook.plan_rules_buffer"


# Rule fields whose change must re-push the parent plan (rules payload):
# quota / max_avail / stay restrictions / closures / OTA opt-out.
# ``real_avail`` / ``plan_avail`` / ``avail_id`` are intentionally NOT
# here — those are technical recomputes that flow from reservation line
# changes; ``plan_avail`` is handled separately below.
_RULE_PLAN_FIELDS = frozenset(
    {
        "quota",
        "max_avail",
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


# Rule fields whose change must re-push the property availability.
# ``plan_avail`` = min(real_avail, quota, max_avail) is the bookable
# count actually shipped to Wubook. A change in ``real_avail`` does NOT
# necessarily change ``plan_avail`` (the cap may absorb it), so we
# trigger on ``plan_avail`` itself to avoid no-op pushes.
_RULE_AVAIL_FIELDS = frozenset({"plan_avail"})


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


class ChannelWubookPmsAvailabilityPlanRuleListener(Component):
    """Cascade listener for ``pms.availability.plan.rule``.

    A rule does not have its own Wubook entity: it is exported as part
    of the parent plan payload. So every change on a rule may schedule:

    * a re-export of its parent **plan** (when business fields change),
    * a re-export of the property **availability** (when ``plan_avail``
      — the bookable count actually shipped to Wubook — changes).

    Both paths coalesce through per-transaction buffers so that massive
    operations (e.g. ``wizard_massive_changes`` touching hundreds of
    rules) end up enqueuing **one** job per plan binding and **one**
    job per (backend × property) pair for availability.
    """

    _name = "channel.wubook.pms.availability.plan.rule.listener"
    _inherit = "base.connector.listener"
    _apply_on = "pms.availability.plan.rule"

    # --- plan-rules buffer helpers ----------------------------------

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
        rule_property_id = record.pms_property_id.id
        for binding in plan.channel_wubook_bind_ids:
            if not binding.external_id:
                # The plan has not been connected yet on this backend.
                continue
            # The plan can be global (shared by N properties) and own
            # one binding per backend. The rule belongs to a single
            # property, so only the binding whose backend covers that
            # property needs the push.
            if binding.backend_id.pms_property_id.id != rule_property_id:
                continue
            self._buffer_plan_export(binding)

    # --- property-availability buffer helpers -----------------------
    # Shares the same buffer key as ``ChannelWubookPmsAvailabilityListener``
    # so that simultaneous triggers from both listeners collapse to a
    # single ``export_record`` per (backend × property) pair within a
    # transaction.

    def _buffer_property_export(self, property_binding):
        cr = self.env.cr
        data = cr.precommit.data
        if _AVAILABILITY_BUFFER_KEY not in data:
            data[_AVAILABILITY_BUFFER_KEY] = {}
            env = self.env
            cr.precommit.add(lambda env=env: _flush_availability_buffer(env))
        data[_AVAILABILITY_BUFFER_KEY].setdefault(property_binding.id, property_binding)

    def _enqueue_property_avail_for_rule(self, record):
        avail = record.avail_id
        if not avail:
            return
        prop = avail.pms_property_id
        room_type = avail.room_type_id
        if not prop or not room_type:
            return
        for property_binding in prop.channel_wubook_bind_ids:
            if not property_binding.external_id:
                continue
            backend = property_binding.backend_id
            room_type_bound = room_type.channel_wubook_bind_ids.filtered(
                lambda b, backend=backend: b.backend_id == backend and b.external_id
            )
            if not room_type_bound:
                continue
            self._buffer_property_export(property_binding)

    # --- event handlers ---------------------------------------------

    @skip_if(lambda self, record, **kwargs: self.no_connector_export(record))
    def on_record_create(self, record, fields=None):
        self._enqueue_for_rule(record)

    @skip_if(lambda self, record, **kwargs: self.no_connector_export(record))
    def on_record_write(self, record, fields=None):
        if not fields:
            return
        changed = set(fields)
        if changed & _RULE_PLAN_FIELDS:
            self._enqueue_for_rule(record)
        if changed & _RULE_AVAIL_FIELDS:
            self._enqueue_property_avail_for_rule(record)

    @skip_if(lambda self, record, **kwargs: self.no_connector_export(record))
    def on_record_unlink(self, record, fields=None):
        # Read the plan now before the rule is gone.
        self._enqueue_for_rule(record)
