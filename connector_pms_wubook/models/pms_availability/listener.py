# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo.addons.component.core import Component
from odoo.addons.component_event.components.event import skip_if

from ..common.wubook_window import accepted_window

# Per-transaction buffer for property-availability exports. Shared with the
# reservation, room, folio and ``pms.inventory.rule`` producers so that
# simultaneous triggers collapse to a single ``export_record`` per
# (backend × property) pair, carrying the union of the nights they touched.
_AVAILABILITY_BUFFER_KEY = "connector_pms_wubook.availability_buffer"


def _flush_availability_buffer(env):
    """Precommit callback: hand each binding the window that was staged for
    it and enqueue **one** ``export_record`` job per binding.
    """
    data = env.cr.precommit.data.pop(_AVAILABILITY_BUFFER_KEY, None)
    if not data:
        return
    for _binding_id, (binding, date_from, date_to) in data.items():
        binding = binding.exists()
        if not binding:
            continue
        binding._wubook_stage_pending_window(date_from, date_to)
        binding.with_delay(
            identity_key=(
                f"wubook_export_property_avail:{binding.backend_id.id}:{binding.odoo_id.id}"
            )
        ).export_record(binding.backend_id, binding.odoo_id)


def _window(date_from, date_to):
    """Fill in the nights a producer did not narrow down.

    Some changes have no natural end — a room switched off, a reservation
    moved between room types — so what they affect is the rest of the
    calendar Wubook holds.
    """
    accepted_from, accepted_to = accepted_window()
    return (date_from or accepted_from, date_to or accepted_to)


def buffer_property_export(env, property_binding, date_from=None, date_to=None):
    """Stage one property-availability export for ``property_binding``.

    Canonical entry point for every producer (listeners, folio importer):
    the buffer lives in ``cr.precommit.data`` so that N contributions in a
    transaction collapse to one job per binding, and ``identity_key``
    collapses bursts spanning several transactions. The windows add up, so
    the job pushes every night the transaction moved and no more.
    """
    date_from, date_to = _window(date_from, date_to)
    cr = env.cr
    data = cr.precommit.data
    if _AVAILABILITY_BUFFER_KEY not in data:
        data[_AVAILABILITY_BUFFER_KEY] = {}
        env_captured = env
        cr.precommit.add(lambda env=env_captured: _flush_availability_buffer(env))
    staged = data[_AVAILABILITY_BUFFER_KEY].get(property_binding.id)
    if staged:
        data[_AVAILABILITY_BUFFER_KEY][property_binding.id] = (
            staged[0],
            min(staged[1], date_from),
            max(staged[2], date_to),
        )
    else:
        data[_AVAILABILITY_BUFFER_KEY][property_binding.id] = (
            property_binding,
            date_from,
            date_to,
        )


def buffer_property_exports(env, pms_property, date_from=None, date_to=None):
    """Stage a property-availability export on every backend connected on
    ``pms_property``, whatever the room types involved.

    For a reassignment the footprint spans the room type the reservation
    LEFT and the one it took, and the event only carries the latter, so
    gating on it would skip the freed side whenever the destination type is
    not mapped on the backend (an internal room, a type not sold online).
    The export only ships what actually moved, so scheduling one too many
    costs a no-op job at worst.
    """
    if not pms_property:
        return
    for property_binding in pms_property.channel_wubook_bind_ids:
        if not property_binding.external_id:
            # Property not yet connected on this backend.
            continue
        buffer_property_export(env, property_binding, date_from, date_to)


def buffer_property_exports_for_rooms(
    env, pms_property, room_types, date_from=None, date_to=None
):
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
        buffer_property_export(env, property_binding, date_from, date_to)


class ChannelWubookPmsAvailabilityListener(Component):
    """Cascade listener for ``pms.availability``.

    Only ``on_record_create`` is wired: a fresh row appears when a night
    that nothing had touched starts being occupied, so what it publishes is
    that night.

    ``on_record_write`` is intentionally NOT handled here. ``real_avail``
    flips on every reservation line change, but what gets shipped to Wubook
    is ``min(real_avail, declared inventory)``, so a ``real_avail`` change
    the cap absorbs is a no-op. The pushes are scheduled by whoever moved
    the value: the reservation and line listeners for ``real_avail``, and
    ``ChannelWubookPmsInventoryRuleListener`` for the inventory.

    Coalescence: same transactional buffer pattern as plan rules. A burst
    of changes across one transaction collapses to one job per (backend ×
    property) pair; bursts across several transactions collapse to at most
    one PENDING job per pair thanks to queue_job's ``identity_key``.
    """

    _name = "channel.wubook.pms.availability.listener"
    _inherit = "base.connector.listener"
    _apply_on = "pms.availability"

    @skip_if(lambda self, record, **kwargs: self.no_connector_export(record))
    def on_record_create(self, record, fields=None):
        buffer_property_exports_for_rooms(
            self.env,
            record.pms_property_id,
            record.room_type_id,
            record.date,
            record.date,
        )
