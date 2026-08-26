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

    def get_all_items(self, mapper, items, parent, to_attr, options):
        """Hand each room the booking level fields it needs.

        Merged here, and not in the adapter, so the adapter stays a faithful
        mirror of the payload: this is the one place that sees both levels. The
        room wins on a clash, should Channex ever state one of these per room.
        """
        booking = {key: parent.source.get(key) for key in self.BOOKING_LEVEL}
        return super().get_all_items(
            mapper,
            [{**booking, **item} for item in items],
            parent,
            to_attr,
            options,
        )
