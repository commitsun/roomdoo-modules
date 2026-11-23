# Copyright 2021 Eric Antones <eantones@nuobit.com>
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import _
from odoo.exceptions import ValidationError

from odoo.addons.component.core import Component
from odoo.addons.connector.components.mapper import mapping, only_create


# REVIEW: move these two functions as a method of a mapper or binder
#         or in a tools library
def get_room_type(mapper, room_id):
    rt_binder = mapper.binder_for("channel.wubook.pms.room.type")
    room_type = rt_binder.to_internal(room_id, unwrap=True)
    assert room_type, (
        f"room_id {room_id} should have been imported in "
        "PmsRoomTypeImporter._import_dependencies"
    )
    return room_type


def get_board_service(mapper, room_type, board):
    """The board service a room type is sold with on Wubook, as the backend
    sets it"""
    bd_binder = mapper.binder_for("channel.wubook.pms.board.service")
    board_service = bd_binder.to_internal(board, unwrap=True)
    assert board_service, (
        f"board_service_id '{board}' should've been imported in "
        "PmsRoomTypeImporter._import_dependencies or "
        "PmsFolioImporter._import_dependencies.\n"
    )
    backend_board = mapper.backend_record.board_service_ids.filtered(
        lambda x: x.board_service_id == board_service and x.room_type_id == room_type
    )
    if not backend_board:
        raise ValidationError(
            _("The Board Service '%(code)s' is not available in Room Type '%(room)s'")
            % {"code": board_service.default_code, "room": room_type.default_code}
        )
    return backend_board


def pad_hour(hour_str):
    """Return the hour Wubook sends ("9:00") zero-padded ("09:00").

    ``pms.reservation.arrival_hour`` only accepts the padded format. What
    is not an hour is returned untouched, to be rejected there instead of
    imported.
    """
    hour, separator, minute = hour_str.partition(":")
    if not separator or not hour.isdigit() or not minute.isdigit():
        return hour_str
    return f"{hour.zfill(2)}:{minute.zfill(2)}"


class ChannelWubookPmsReservationMapperImport(Component):
    _name = "channel.wubook.pms.reservation.mapper.import"
    _inherit = "channel.wubook.mapper.import"

    _apply_on = "channel.wubook.pms.reservation"

    direct = [
        ("occupancy", "adults"),
        ("ota_reservation_code", "ota_reservation_code"),
        ("children", "children"),
    ]

    children = [
        ("lines", "reservation_line_ids", "channel.wubook.pms.reservation.line"),
    ]

    @mapping
    def external_reference(self, record):
        # A channel that sends a locator of its own besides the reservation
        # code looks for it on the invoice, which prints the external
        # reference. Without one, pms computes it from the reservation code.
        # Not only on create: a modification of the booking creates new
        # reservations in the existing folio. The folio child mapper drops it
        # for the reservations that already exist, so their reference is never
        # overwritten.
        locator = record.get("ota_locator")
        code = record.get("ota_reservation_code")
        if locator and locator != code:
            return {"external_reference": f"{code} / {locator}" if code else locator}

    @only_create
    @mapping
    def blocked(self, record):
        # Block False if the reservation origin is from the Wubook
        # True if the reservation origin is from the OTA
        if record["id_channel"] == 0:
            return {"blocked": False}
        else:
            return {"blocked": True}

    @only_create
    @mapping
    def pms_property_id(self, record):
        return {"pms_property_id": self.backend_record.pms_property_id.id}

    @mapping
    def room_type_id(self, record):
        return {"room_type_id": get_room_type(self, record["room_id"]).id}

    @mapping
    def board_service(self, record):
        if record["board"]:
            room_type = get_room_type(self, record["room_id"])
            return {
                "board_service_id": get_board_service(
                    self, room_type, record["board"]
                ).board_service_id.id
            }

    @mapping
    def dates(self, record):
        if record["arrival_hour"]:
            if record["arrival_hour"] == "24:00":
                record["arrival_hour"] = "23:59"
            return {
                "arrival_hour": pad_hour(record["arrival_hour"]),
            }

    @mapping
    def requests(self, record):
        return {
            "partner_requests": record["customer_notes"],
        }

    @mapping
    def pricelist_id(self, record):
        pricelist_id = False
        if record["rate_id"]:
            binder = self.binder_for("channel.wubook.product.pricelist")
            pricelist = binder.to_internal(record["rate_id"], unwrap=True)
            assert pricelist, (
                f"rate_id {record['rate_id']} should have been imported in "
                "ProductPricelistImporter._import_dependencies"
            )
            pricelist_id = pricelist.id

            if pricelist_id:
                return {"pricelist_id": pricelist_id}


class ChannelWubookPmsReservationChildMapperImport(Component):
    _name = "channel.wubook.pms.reservation.child.mapper.import"
    _inherit = "channel.wubook.child.mapper.import"
    _apply_on = "channel.wubook.pms.reservation.line"
