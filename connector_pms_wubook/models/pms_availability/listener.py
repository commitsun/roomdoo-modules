# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo.addons.component.core import Component
from odoo.addons.component_event.components.event import skip_if

# Per-transaction buffer for property-availability exports. Shared with the
# reservation, room and ``pms.inventory.rule`` listeners so that simultaneous
# triggers collapse to a single ``export_record`` per (backend × property)
# pair.
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


def buffer_property_export(env, property_binding):
    """Stage one property-availability export for ``property_binding``.

    Canonical entry point for every producer (listeners, folio importer):
    the buffer lives in ``cr.precommit.data`` so that N contributions in a
    transaction collapse to one job per binding, and ``identity_key``
    collapses bursts spanning several transactions.
    """
    cr = env.cr
    data = cr.precommit.data
    if _AVAILABILITY_BUFFER_KEY not in data:
        data[_AVAILABILITY_BUFFER_KEY] = {}
        env_captured = env
        cr.precommit.add(lambda env=env_captured: _flush_availability_buffer(env))
    data[_AVAILABILITY_BUFFER_KEY].setdefault(property_binding.id, property_binding)


def buffer_property_exports(env, pms_property):
    """Stage a property-availability export on every backend connected on
    ``pms_property``, whatever the room types involved.

    For a reassignment the footprint spans the room type the reservation
    LEFT and the one it took, and the event only carries the latter, so
    gating on it would skip the freed side whenever the destination type is
    not mapped on the backend (an internal room, a type not sold online).
    The export only ships bindings whose value actually moved, so scheduling
    one too many costs a no-op job at worst.
    """
    if not pms_property:
        return
    for property_binding in pms_property.channel_wubook_bind_ids:
        if not property_binding.external_id:
            # Property not yet connected on this backend.
            continue
        buffer_property_export(env, property_binding)


def buffer_property_exports_for_rooms(env, pms_property, room_types):
    """Stage a property-availability export on every backend connected on
    ``pms_property`` that also has at least one of ``room_types`` bound.

    A backend only sells the room types it has mapped, so a change limited
    to unbound types has nothing to publish.
    """
    if not pms_property or not room_types:
        return
    for property_binding in pms_property.channel_wubook_bind_ids:
        if not property_binding.external_id:
            # Property not yet connected on this backend.
            continue
        backend = property_binding.backend_id
        bound = room_types.filtered(
            lambda rt, backend=backend: any(
                b.backend_id == backend and b.external_id
                for b in rt.channel_wubook_bind_ids
            )
        )
        if not bound:
            continue
        buffer_property_export(env, property_binding)


class ChannelWubookPmsAvailabilityListener(Component):
    """Cascade listener for ``pms.availability``.

    Only ``on_record_create`` is wired: when a fresh ``pms.availability``
    appears (typically through ``_compute_avail_id`` expanding the
    calendar to a new date/room_type), buffer an initial push to every
    Wubook backend connected on its property — provided the room_type
    is also bound.

    ``on_record_write`` is intentionally NOT handled here. ``real_avail``
    flips on every reservation line change, but what gets shipped to
    Wubook is ``sale_avail`` = min(real_avail, declared inventory), so a
    ``real_avail`` change the cap absorbs is a no-op. The pushes are
    scheduled by whoever moved the value: the reservation and line
    listeners for ``real_avail``, and
    ``ChannelWubookPmsInventoryRuleListener`` for the inventory.

    Coalescence: same transactional buffer pattern as plan rules. A
    burst of changes across one transaction collapses to one job per
    (backend × property) pair; bursts across several transactions
    collapse to at most one PENDING job per pair thanks to queue_job's
    ``identity_key``.
    """

    _name = "channel.wubook.pms.availability.listener"
    _inherit = "base.connector.listener"
    _apply_on = "pms.availability"

    def _enqueue_property_exports(self, record):
        """For each Wubook backend connected on ``record.pms_property_id``
        and where ``record.room_type_id`` is also bound, buffer one
        property-availability export.
        """
        buffer_property_exports_for_rooms(
            self.env, record.pms_property_id, record.room_type_id
        )

    @skip_if(lambda self, record, **kwargs: self.no_connector_export(record))
    def on_record_create(self, record, fields=None):
        self._enqueue_property_exports(record)
