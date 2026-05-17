# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo.addons.component.core import Component
from odoo.addons.component_event.components.event import skip_if

# Per-transaction buffer for property-availability exports. Shared with
# ``ChannelWubookPmsAvailabilityPlanRuleListener`` (which fires on
# ``plan_avail`` changes) so that simultaneous triggers collapse to a
# single ``export_record`` per (backend × property) pair.
_AVAILABILITY_BUFFER_KEY = "connector_pms_wubook.availability_buffer"


def _flush_availability_buffer(env):
    """Precommit callback: enqueue **one** ``export_record`` job per
    ``channel.wubook.pms.property.availability`` binding accumulated
    during the transaction.
    """
    data = env.cr.precommit.data.pop(_AVAILABILITY_BUFFER_KEY, None)
    if not data:
        return
    for _binding_id, binding in data.items():
        binding = binding.exists()
        if not binding:
            continue
        binding.with_delay(
            identity_key=(
                f"wubook_export_property_avail:{binding.backend_id.id}:{binding.odoo_id.id}"
            )
        ).export_record(binding.backend_id, binding.odoo_id)


class ChannelWubookPmsAvailabilityListener(Component):
    """Cascade listener for ``pms.availability``.

    Only ``on_record_create`` is wired: when a fresh ``pms.availability``
    appears (typically through ``_compute_avail_id`` expanding the
    calendar to a new date/room_type), buffer an initial push to every
    Wubook backend connected on its property — provided the room_type
    is also bound.

    ``on_record_write`` is intentionally NOT handled here. ``real_avail``
    flips on every reservation line change, but only ``plan_avail``
    (= min(real_avail, quota, max_avail)) is what actually gets shipped
    to Wubook; a real_avail change that the cap absorbs is a no-op.
    The ``plan_avail`` recompute lands on ``pms.availability.plan.rule``
    so ``ChannelWubookPmsAvailabilityPlanRuleListener`` is the one that
    triggers the property availability push when needed.

    Coalescence: same transactional buffer pattern as plan rules. A
    burst of changes across one transaction collapses to one job per
    (backend × property) pair; bursts across several transactions
    collapse to at most one PENDING job per pair thanks to queue_job's
    ``identity_key``.
    """

    _name = "channel.wubook.pms.availability.listener"
    _inherit = "base.connector.listener"
    _apply_on = "pms.availability"

    def _buffer_property_export(self, property_binding):
        cr = self.env.cr
        data = cr.precommit.data
        if _AVAILABILITY_BUFFER_KEY not in data:
            data[_AVAILABILITY_BUFFER_KEY] = {}
            env = self.env
            cr.precommit.add(lambda env=env: _flush_availability_buffer(env))
        data[_AVAILABILITY_BUFFER_KEY].setdefault(property_binding.id, property_binding)

    def _enqueue_property_exports(self, record):
        """For each Wubook backend connected on ``record.pms_property_id``
        and where ``record.room_type_id`` is also bound, buffer one
        property-availability export.
        """
        prop = record.pms_property_id
        room_type = record.room_type_id
        if not prop or not room_type:
            return
        for property_binding in prop.channel_wubook_bind_ids:
            if not property_binding.external_id:
                # Property not yet connected on this backend.
                continue
            backend = property_binding.backend_id
            room_type_bound = room_type.channel_wubook_bind_ids.filtered(
                lambda b, backend=backend: b.backend_id == backend and b.external_id
            )
            if not room_type_bound:
                continue
            self._buffer_property_export(property_binding)

    @skip_if(lambda self, record, **kwargs: self.no_connector_export(record))
    def on_record_create(self, record, fields=None):
        self._enqueue_property_exports(record)
