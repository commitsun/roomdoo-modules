# Copyright 2021 Eric Antones <eantones@nuobit.com>
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import _
from odoo.exceptions import ValidationError

from odoo.addons.component.core import Component
from odoo.addons.connector.components.mapper import mapping


class ChannelWubookPmsRoomTypeMapperExport(Component):
    _name = "channel.wubook.pms.room.type.mapper.export"
    _inherit = "channel.wubook.mapper.export"

    _apply_on = "channel.wubook.pms.room.type"

    direct = [
        ("name", "name"),
        ("occupancy", "occupancy"),
        ("list_price", "price"),
        ("min_price", "min_price"),
        ("max_price", "max_price"),
        ("default_availability", "availability"),
    ]

    @mapping
    def shortname(self, record):
        # Wubook enforces a 4-char limit on the room shortname; surface a
        # clear validation error instead of letting WuBook reject the
        # XMLRPC call with a cryptic "Shortname: max 4 chars please".
        code = record.default_code or ""
        if len(code) > 4:
            raise ValidationError(
                _(
                    "Room type '%(name)s' has default code '%(code)s' "
                    "(%(length)d chars). Wubook accepts a maximum of 4 "
                    "characters for the room shortname. Please shorten "
                    "the default code before connecting this room type."
                )
                % {
                    "name": record.display_name,
                    "code": code,
                    "length": len(code),
                }
            )
        if not code:
            raise ValidationError(
                _(
                    "Room type '%s' has no default code. Wubook requires "
                    "a non-empty shortname (up to 4 chars)."
                )
                % record.display_name
            )
        return {"shortname": code}

    def _board_services(self, record):
        """The board services of the room type on this backend that Wubook
        knows: the ones bound to it"""
        return self.backend_record.board_service_ids.filtered(
            lambda board: board.room_type_id == record.odoo_id
            and board.board_service_id.channel_wubook_bind_ids.filtered(
                lambda binding: binding.backend_id == self.backend_record
            )
        )

    def _board_external_id(self, board_service, record):
        bs_binder = self.binder_for("channel.wubook.pms.board.service")
        external_id = bs_binder.to_external(board_service, wrap=True)
        if not external_id:
            raise ValidationError(
                _(
                    "External record of Board Service [%(code)s] %(room)s "
                    "does not exists. It should be exported in "
                    "_export_dependencies"
                )
                % {"code": board_service.default_code, "room": record.name}
            )
        return external_id

    @mapping
    def boards(self, record):
        capacity = record.odoo_id.get_room_type_capacity(
            pms_property_id=self.backend_record.pms_property_id.id
        )
        return {
            "boards": {
                self._board_external_id(board.board_service_id, record): {
                    "dtype": 2,
                    "value": board.amount * capacity,
                }
                for board in self._board_services(record)
            }
        }

    @mapping
    def default_board_service(self, record):
        default_board = self.backend_record.board_service_ids.filtered(
            lambda board: board.room_type_id == record.odoo_id and board.by_default
        )
        if not default_board:
            return {"board": "nb"}
        return {
            "board": self._board_external_id(default_board.board_service_id, record)
        }

    @mapping
    def room_type_class(self, record):
        room_class = record.class_id
        rc_binder = self.binder_for("channel.wubook.pms.room.type.class")
        external_id = rc_binder.to_external(room_class, wrap=True)
        if not external_id:
            raise ValidationError(
                _(
                    "External record of Room Class [%(code)s] %(room)s "
                    "does not exists. It should be exported in "
                    "_export_dependencies"
                )
                % {"code": room_class.id, "room": room_class.name}
            )
        return {"rtype": external_id}

    @mapping
    def woodoo(self, record):
        return {"woodoo": 0}
