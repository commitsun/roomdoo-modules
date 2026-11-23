# Copyright 2021 Eric Antones <eantones@nuobit.com>
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).


from odoo.addons.component.core import Component


class ChannelWubookPmsRoomTypeDelayedBatchImporter(Component):
    _name = "channel.wubook.pms.room.type.delayed.batch.importer"
    _inherit = "channel.wubook.delayed.batch.importer"

    _apply_on = "channel.wubook.pms.room.type"


class ChannelWubookPmsRoomTypeDirectBatchImporter(Component):
    _name = "channel.wubook.pms.room.type.direct.batch.importer"
    _inherit = "channel.wubook.direct.batch.importer"

    _apply_on = "channel.wubook.pms.room.type"


class ChannelWubookPmsRoomTypeImporter(Component):
    _name = "channel.wubook.pms.room.type.importer"
    _inherit = "channel.wubook.importer"

    _apply_on = "channel.wubook.pms.room.type"

    def _import_dependencies(self, external_data, external_fields):
        self._import_dependency(
            external_data["rtype"], "channel.wubook.pms.room.type.class"
        )
        for board in external_data["boards"]:
            self._import_dependency(board["id"], "channel.wubook.pms.board.service")
        self._wubook_boards = external_data["boards"]

    def _after_import(self, binding):
        """The board services Wubook sells the room type with are set on the
        backend the first time the room type comes in, at the price of the
        board service"""
        backend_boards = self.backend_record.board_service_ids
        if backend_boards.filtered(lambda b: b.room_type_id == binding.odoo_id):
            return
        binder = self.binder_for("channel.wubook.pms.board.service")
        values = []
        for board in self._wubook_boards:
            board_service = binder.to_internal(board["id"], unwrap=True)
            values.append(
                {
                    "backend_id": self.backend_record.id,
                    "room_type_id": binding.odoo_id.id,
                    "board_service_id": board_service.id,
                    "amount": board_service.list_price,
                    "by_default": bool(board["default"]),
                }
            )
        backend_boards.create(values)
