# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo.addons.component.core import Component
from odoo.addons.connector.components.mapper import mapping, only_create

from ..booking_revision.booking_revision import _channex_datetime


class ChannelChannexPmsFolioMapperImport(Component):
    _name = "channel.channex.pms.folio.mapper.import"
    _inherit = "channel.channex.mapper.import"

    _apply_on = "channel.channex.pms.folio"

    children = [
        ("rooms", "reservation_ids", "pms.reservation"),
    ]

    @only_create
    @mapping
    def backend_id(self, record):
        return {"backend_id": self.backend_record.id}

    @only_create
    @mapping
    def pms_property_id(self, record):
        return {"pms_property_id": self.backend_record.pms_property_id.id}

    @mapping
    def revision(self, record):
        """Stamp which message the folio is at, in the same write as the data
        that message carried."""
        return {
            "revision_external_id": record["id"],
            "revision_inserted_at": _channex_datetime(record.get("inserted_at")),
        }

    @mapping
    def customer(self, record):
        customer = record.get("customer") or {}
        name = ", ".join(
            part for part in (customer.get("surname"), customer.get("name")) if part
        )
        values = {
            # pms rejects a folio with no customer name, and losing the booking
            # over a name the OTA did not send is worse than labelling it with
            # the reference the OTA did send.
            "partner_name": name or record.get("unique_id"),
            "email": customer.get("mail"),
            "mobile": customer.get("phone"),
        }
        lang = self.env["res.lang"].search(
            [("iso_code", "=", customer.get("language"))], limit=1
        )
        if lang:
            values["lang"] = lang.code
        return values

    @only_create
    @mapping
    def agency(self, record):
        """The partner the booking belongs to, or nothing at all.

        Never a blocker: an unattributed booking is a booking someone can fix,
        a rejected one is a booking lost. Resolved through the channel, which
        is the only identifier of the three Channex uses that the mapping hangs
        off. Falling back to ``ota_name`` is pending: whether ``channel_id``
        really travels on every message is not documented, and guessing a
        second path before seeing one arrive would be guessing twice.
        """
        channel = (
            self.env["channel.channex.channel"]
            # Deactivated channels are searched too. A channel gone from
            # Channex is deactivated and never deleted for exactly this: the
            # bookings it sold keep resolving to their partner.
            .with_context(active_test=False)
            .search(
                [
                    ("backend_id", "=", self.backend_record.id),
                    ("external_id", "=", record.get("channel_id")),
                ],
                limit=1,
            )
        )
        if not channel.agency_id:
            return {}
        return {
            "agency_id": channel.agency_id.id,
            "sale_channel_origin_id": channel.agency_id.sale_channel_id.id,
        }


class ChannelChannexPmsFolioChildMapperImport(Component):
    """Each room of the message is a reservation of the folio."""

    _name = "channel.channex.pms.folio.child.mapper.import"
    _inherit = "channel.channex.child.mapper.import"

    _apply_on = "pms.reservation"

    # Stated once for the whole booking by Channex, needed on every reservation
    # by pms.
    BOOKING_LEVEL = ("ota_reservation_code", "arrival_hour", "notes")

    #: What a room is recognised by. Neither of the two ids Channex puts on a
    #: room survives a modification, so the reservation a room stands for is
    #: deduced from what it is, and these are the three that make a room a
    #: different room rather than the same one changed.
    IDENTITY = ("room_type_id", "checkin", "checkout")

    def get_all_items(self, mapper, items, parent, to_attr, options):
        """Hand each room the booking level fields it needs.

        Merged here, and not in the adapter, so the adapter stays a faithful
        mirror of the payload: this is the one place that sees both levels. The
        room wins on a clash, should Channex ever state one of these per room.
        """
        booking = {key: parent.source.get(key) for key in self.BOOKING_LEVEL}
        items = [{**booking, **item} for item in items]
        binding = options.get("binding")
        if not binding:
            return super().get_all_items(mapper, items, parent, to_attr, options)
        return self._channex_reconcile(mapper, items, parent, to_attr, options, binding)

    def skip_item(self, map_record):
        """A room the message itself says is cancelled.

        There is nothing to write for it: on a folio being created it simply is
        not one of its reservations, and on one being modified the reservation
        it stood for is left over below, and cancelled there.
        """
        return bool(map_record.source.get("is_cancelled"))

    def format_items(self, items_values):
        return [
            (1, values.pop("id"), values) if values.get("id") else (0, 0, values)
            for values in items_values
        ]

    # -- modifying an existing folio -----------------------------------------

    def _channex_reconcile(self, mapper, items, parent, to_attr, options, binding):
        """Say which reservation each room of the message is, and cancel the rest.

        A room that is recognisably one of the reservations of the folio updates
        it. A room that is not is a reservation the folio does not have yet, and
        a reservation no room accounts for is a reservation the booking no
        longer has.
        """
        # Reservations cancelled by an earlier version of this same booking are
        # not candidates: they are what the booking used to be, and reviving one
        # would re-occupy a room the hotel has already sold again.
        pending = binding.reservation_ids.filtered(lambda r: r.state != "cancel")
        mapped = []
        for item in items:
            map_record = mapper.map_record(item, parent=parent)
            if self.skip_item(map_record):
                continue
            values = self.get_item_values(map_record, to_attr, options)
            reservation = self._channex_match(values, pending)
            if reservation:
                pending -= reservation
                values = self._channex_update_values(values, reservation)
                values["id"] = reservation.id
            else:
                # A reservation the folio is getting now is a reservation being
                # created, and the mappings that only run on creation -- the
                # block against manual edits among them -- have to run for it.
                # Nothing propagates that on this path.
                values = map_record.values(**dict(options, for_create=True))
            if values:
                mapped.append(values)
        self._channex_cancel(pending)
        return mapped

    def _channex_match(self, values, reservations):
        """The reservation this room is, or nothing."""
        identity = (
            values["room_type_id"],
            str(values["checkin"]),
            str(values["checkout"]),
        )
        for reservation in reservations:
            if identity == (
                reservation.room_type_id.id,
                str(reservation.checkin),
                str(reservation.checkout),
            ):
                return reservation
        return self.env["pms.reservation"]

    def _channex_update_values(self, values, reservation):
        """What to write on a reservation that is staying.

        The three fields the reservation was recognised by are dropped: they
        already hold these values, and writing dates or room type sends pms off
        rebuilding the nights of the stay, which is the one thing that must not
        happen to prices the OTA sold. The prices themselves go onto the lines
        that are already there, one per night, because the nights are the same
        nights.
        """
        values = {
            key: value for key, value in values.items() if key not in self.IDENTITY
        }
        nights = values.pop("reservation_line_ids", None)
        if nights is None:
            return values
        lines = {str(line.date): line for line in reservation.reservation_line_ids}
        commands = []
        for _op, _id, night in nights:
            line = lines.get(night["date"])
            assert line, (
                f"night {night['date']} is not a line of reservation "
                f"{reservation.id}, which was matched on these very nights"
            )
            commands.append((1, line.id, {"price": night["price"]}))
        values["reservation_line_ids"] = commands
        return values

    def _channex_cancel(self, reservations):
        """Reservations of the folio the message no longer accounts for.

        Cancelled and never deleted: it is what the hotel worked with until now,
        and someone will want to see it. ``modified`` in the context is what
        tells pms this is not a guest cancelling, so no cancellation penalty is
        charged for it.

        A stay already under way cannot be cancelled, and that is recognised
        before any of this runs, on the message itself. Nothing is checked here
        again: pms refuses it anyway, and the message says so.
        """
        if not reservations:
            return
        reservations.with_context(modified=True).action_cancel()
