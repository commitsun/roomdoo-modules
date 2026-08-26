# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo.addons.component.core import Component
from odoo.addons.connector.components.mapper import mapping, only_create

from ..booking_revision.booking_revision import _channex_nights


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
        """Infants are left out: Channex counts them apart from children
        precisely because they do not take a place, and pms has no third
        number to put them in."""
        occupancy = record.get("occupancy") or {}
        return {
            "adults": occupancy.get("adults") or 1,
            "children": occupancy.get("children") or 0,
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

        The nights come from the stay and the prices from the breakdown, and the
        two are known to agree: a message whose breakdown does not cover its own
        stay exactly is reported on the revision and never reaches here. Nothing
        is derived, defaulted or completed.
        """
        days = record["days"]
        return {
            "reservation_line_ids": [
                (0, 0, {"date": night, "price": float(days[night])})
                for night in _channex_nights(record)
            ]
        }
