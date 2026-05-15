# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo.addons.component.core import Component
from odoo.addons.component_event.components.event import skip_if

# Per-transaction buffer for property-availability exports triggered by
# changes on ``pms.availability``. Replaces the legacy
# ``_scheduler_export_avail`` cron with immediate, listener-driven
# pushes. The buffer is keyed by
# ``channel.wubook.pms.property.availability`` binding id; the flush
# enqueues one ``export_record`` per binding with a coarse
# ``identity_key`` so back-to-back changes spanning many transactions
# collapse to at most one pending job per binding.
_AVAILABILITY_BUFFER_KEY = "connector_pms_wubook.availability_buffer"

# Fields on ``pms.availability`` whose change requires re-pushing the
# availability counts to Wubook. ``real_avail`` is the stored computed
# field that recomputes whenever a reservation line is created /
# updated / cancelled in a way that flips ``occupies_availability``.
# Membership on ``avail_rule_ids`` is intentionally NOT here — plan
# rule changes are coalesced separately by
# ``pms_availability_plan_rule.listener``.
_AVAILABILITY_RELEVANT_FIELDS = {"real_avail"}


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
            identity_key="wubook_export_property_avail:%s:%s"
            % (binding.backend_id.id, binding.odoo_id.id)
        ).export_record(binding.backend_id, binding.odoo_id)


class ChannelWubookPmsAvailabilityListener(Component):
    """Cascade listener for ``pms.availability``.

    Replaces the legacy ``_scheduler_export_avail`` cron. Any change on
    a ``pms.availability`` row (typically a recompute of ``real_avail``
    after a reservation line flip) triggers a coalesced push to every
    Wubook backend connected on its property — provided the affected
    ``room_type`` is also bound on that backend.

    Coalescence: same transactional buffer pattern as items / rules. A
    burst of reservation changes across one transaction collapses to
    one job per (backend × property) pair; bursts across several
    transactions collapse to at most one PENDING job per pair thanks to
    queue_job's ``identity_key``.
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
            cr.precommit.add(
                lambda env=env: _flush_availability_buffer(env)
            )
        data[_AVAILABILITY_BUFFER_KEY].setdefault(
            property_binding.id, property_binding
        )

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
                lambda b, backend=backend: b.backend_id == backend
                and b.external_id
            )
            if not room_type_bound:
                continue
            self._buffer_property_export(property_binding)

    @skip_if(lambda self, record, **kwargs: self.no_connector_export(record))
    def on_record_create(self, record, fields=None):
        self._enqueue_property_exports(record)

    @skip_if(lambda self, record, **kwargs: self.no_connector_export(record))
    def on_record_write(self, record, fields=None):
        if not fields or not (
            set(fields) & _AVAILABILITY_RELEVANT_FIELDS
        ):
            return
        self._enqueue_property_exports(record)
