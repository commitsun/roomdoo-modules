# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo.addons.component.core import Component
from odoo.addons.connector.components.mapper import mapping, only_create

from ..booking_revision.booking_revision import _channex_guests, _channex_nights


class ChannelChannexPmsReservationMapperImport(Component):
    """A room of a booking, as a reservation of the folio.

    Applied on ``pms.reservation`` and not on a binding of it: Channex has no
    identifier for one room of a booking that survives a modification, and
    nothing is ever pushed the other way, so there is nothing to bind. What is
    bound is the booking, on the folio.
    """

    _name = "channel.channex.pms.reservation.mapper.import"
    _inherit = "channel.channex.mapper.import"

    _apply_on = "pms.reservation"

    @only_create
    @mapping
    def pms_property_id(self, record):
        return {"pms_property_id": self.backend_record.pms_property_id.id}

    @only_create
    @mapping
    def blocked(self, record):
        # Not ours to retouch by hand: pms then refuses manual edits of dates,
        # room type and prices.
        return {"blocked": True}

    @mapping
    def room_type_id(self, record):
        binder = self.binder_for("channel.channex.pms.room.type")
        room_type = binder.to_internal(record["room_type_id"], unwrap=True)
        assert room_type, (
            f"room_type_id {record['room_type_id']} reached the mapper unmapped; "
            "it should have been reported on the revision instead"
        )
        return {"room_type_id": room_type.id}

    @mapping
    def dates(self, record):
        return {
            "checkin": record["checkin_date"],
            "checkout": record["checkout_date"],
        }

    @mapping
    def occupancy(self, record):
        adults, children = _channex_guests(record)
        return {
            "adults": adults,
            "children": children,
            # As the channel declares them. Whether a channel counts the
            # infants left out above among them is not documented, so they are
            # kept as they come.
            "children_ages": (record.get("occupancy") or {}).get("ages") or False,
        }

    @mapping
    def rate_plan(self, record):
        """The pricelist and the board service the room was sold under.

        Stated, and never left to pms: on its own it gives the reservation the
        default board service of its pricelist, and charges it on top of a
        price that already includes the one sold. Room only is no board service
        at all.

        Only a cancellation gets here without a rate plan of ours, and then it
        is pms that says, for a stay that is not happening.
        """
        rate_plan = self._channex_rate_plan(record)
        if not rate_plan:
            return {}
        board_service = rate_plan.board_service_id
        if board_service == board_service._room_only_board_service():
            board_service = board_service.browse()
        return {
            "pricelist_id": rate_plan.pricelist_id.id,
            "board_service_id": board_service.id,
        }

    @mapping
    def ota_reservation_code(self, record):
        # Reaches the folio on its own: pms derives the external reference of
        # the folio from the one on its reservations.
        return {"ota_reservation_code": record.get("ota_reservation_code")}

    @mapping
    def requests(self, record):
        return {"partner_requests": record.get("notes")}

    @mapping
    def arrival_hour(self, record):
        hour = record.get("arrival_hour")
        if not hour:
            return {}
        # Midnight arrives as the end of the day Channex still calls 24:00.
        return {"arrival_hour": "23:59" if hour == "24:00" else hour}

    @mapping
    def reservation_line_ids(self, record):
        """One line per night of the stay, priced as the OTA sold it.

        The OTA sold the room and its board service together, at one price a
        night, and pms keeps the two apart: the board service on lines of its
        own, at what the rate plan published for it, and the room on these, at
        the rest. Together they come to what the OTA sold, to the cent.

        For a booking coming in or being modified the nights and the breakdown
        are known to agree, and every night to pay for its board service: a
        message where they do not is reported on the revision and never reaches
        here. Nothing is defaulted or completed.

        A cancellation is the exception, and only because nothing is allowed to
        hold one up. A night it leaves unpriced, or priced below its board
        service, still gets its line, with the date and no more, exactly as pms
        fills one in, and pms prices it from the pricelist. It is a price
        nobody will ever be charged: the stay is not happening.

        The line has to be there even so. pms works the nights of a reservation
        out from the stay only as long as nothing states them, and stating some
        of them leaves the rest uncomputed and the reservation refused.
        """
        days = record.get("days") or {}
        rate_plan = self._channex_rate_plan(record)
        adults, children = _channex_guests(record)
        lines = []
        for night in _channex_nights(record):
            line = {"date": night}
            if night in days:
                price = float(days[night])
                if rate_plan:
                    price = rate_plan._channex_room_price(
                        night, price, adults, children
                    )
                if price > 0:
                    line["price"] = price
            lines.append((0, 0, line))
        return {"reservation_line_ids": lines}

    def _channex_rate_plan(self, record):
        return self.env["channel.channex.rate.plan"]._channex_booked(
            self.backend_record, record
        )
