# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo.fields import Date

from odoo.addons.component.core import Component
from odoo.addons.component_event.components.event import skip_if

from ..pms_availability.listener import buffer_property_exports_for_rooms

# Fields whose change moves the bookable count Wubook is told about.
# ``sale_channel_id`` and ``agency_id`` are here because a rule moving in or
# out of the general scope changes what applies to the property as a whole,
# which is the scope the connector exports.
_INVENTORY_RELEVANT_FIELDS = {
    "quota",
    "max_avail",
    "date_from",
    "date_to",
    "room_type_id",
    "pms_property_id",
    "sale_channel_id",
    "agency_id",
    "active",
}


class ChannelWubookPmsInventoryRuleListener(Component):
    """Listener for ``pms.inventory.rule``.

    ``channel.wubook.pms.availability.sale_avail`` is a stored compute that
    reads the resolved inventory, but the inventory lives in a model with no
    relation to ``pms.availability``, so there is no ``@api.depends`` path
    that could refresh it. This listener is that path.

    Only rules at the GENERAL scope matter: what the connector ships is the
    availability of the property.

    The fan-out is bounded on purpose. A rule can span a year and several room
    types, but the export buffer keys on the property binding, so the flush
    enqueues ONE job per property and backend, and the recompute stops at
    today: a past night is not for sale.
    """

    _name = "channel.wubook.pms.inventory.rule.listener"
    _inherit = "base.connector.listener"
    _apply_on = "pms.inventory.rule"

    def _enqueue_for_rule(self, record):
        if record.sale_channel_id or record.agency_id:
            return
        pms_property = record.pms_property_id
        room_type = record.room_type_id
        if not pms_property or not room_type:
            return
        date_from = max(record.date_from, Date.today())
        if date_from > record.date_to:
            # Rule entirely in the past: nothing left to sell on it.
            return
        bindings = self.env["channel.wubook.pms.availability"].search(
            [
                ("pms_property_id", "=", pms_property.id),
                ("room_type_id", "=", room_type.id),
                ("date", ">=", date_from),
                ("date", "<=", record.date_to),
            ]
        )
        if bindings:
            self.env.add_to_compute(
                bindings._fields["sale_avail"],
                bindings,
            )
        buffer_property_exports_for_rooms(self.env, pms_property, room_type)

    @skip_if(lambda self, record, **kwargs: self.no_connector_export(record))
    def on_record_create(self, record, fields=None):
        self._enqueue_for_rule(record)

    @skip_if(lambda self, record, **kwargs: self.no_connector_export(record))
    def on_record_write(self, record, fields=None):
        if not fields or not (set(fields) & _INVENTORY_RELEVANT_FIELDS):
            return
        self._enqueue_for_rule(record)

    @skip_if(lambda self, record, **kwargs: self.no_connector_export(record))
    def on_record_unlink(self, record, fields=None):
        # Read the scope before the rule is gone: deleting it lifts its cap.
        self._enqueue_for_rule(record)
