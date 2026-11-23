# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo.addons.component.core import Component
from odoo.addons.component_event.components.event import skip_if


class ChannelWubookBackendBoardServiceListener(Component):
    """The board services of a room type go out with the room type, so a
    change in them exports the room type again, to the backend it was made on.
    Room types not exported yet are left for their own export."""

    _name = "channel.wubook.backend.board.service.listener"
    _inherit = "base.connector.listener"
    _apply_on = "channel.wubook.backend.board.service"

    def _export_room_types(self, records):
        for record in records:
            for binding in record.room_type_id.channel_wubook_bind_ids.filtered(
                lambda b, backend=record.backend_id: b.backend_id == backend
                and b.external_id
            ):
                binding.with_delay().export_record(
                    binding.backend_id, record.room_type_id
                )

    @skip_if(lambda self, record, **kwargs: self.no_connector_export(record))
    def on_record_create(self, record, fields=None):
        self._export_room_types(record)

    @skip_if(lambda self, record, **kwargs: self.no_connector_export(record))
    def on_record_write(self, record, fields=None):
        self._export_room_types(record)

    @skip_if(lambda self, record, **kwargs: self.no_connector_export(record))
    def on_record_unlink(self, record):
        self._export_room_types(record)
