# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo.addons.component.core import Component
from odoo.addons.component_event.components.event import skip_if


class ChannelChannexPmsRoomTypeListener(Component):
    """Keeps Channex in step with the life cycle of a room type.

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
        if not fields or "active" not in fields:
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
