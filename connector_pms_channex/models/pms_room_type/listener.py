# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo.addons.component.core import Component
from odoo.addons.component_event.components.event import skip_if

#: The inventory a room type falls back on where no rule says otherwise, which
#: is most nights of most room types.
_INVENTORY_DEFAULT_FIELDS = {"default_quota", "default_max_avail"}


class ChannelChannexPmsRoomTypeListener(Component):
    """Keeps Channex in step with the life cycle of a room type, and with the
    inventory it falls back on.

    Only the life cycle: creating, archiving and deleting. A room type that
    merely changes its name is not pushed yet.
    """

    _name = "channel.channex.pms.room.type.listener"
    _inherit = "base.connector.listener"
    _apply_on = "pms.room.type"

    @skip_if(lambda self, record, **kwargs: self.no_connector_export(record))
    def on_record_create(self, record, fields=None):
        record._channex_schedule_sync()

    @skip_if(lambda self, record, **kwargs: self.no_connector_export(record))
    def on_record_write(self, record, fields=None):
        if not fields:
            return
        if _INVENTORY_DEFAULT_FIELDS & set(fields):
            # They are the property's cap where no rule says otherwise, so they
            # also decide whether an agency's cap is any stricter.
            record._channex_stage_availability()
            record._channex_stage_channel_caps()
        if "active" not in fields:
            return
        if record.active:
            record._channex_schedule_sync()
        else:
            record._channex_remove_from_channel()

    @skip_if(lambda self, record, **kwargs: self.no_connector_export(record))
    def on_record_unlink(self, record, fields=None):
        # Fired before the delete, which is the only moment the bindings and
        # their external ids can still be read.
        record._channex_remove_from_channel()
