# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import api, fields, models


class ChannelChannexChannel(models.Model):
    """A channel as it exists on Channex.

    Discovered, never created from here: only Channex knows which OTAs can
    actually be connected, and each one has its own mapping screen. What Odoo
    adds is the partner every booking of that channel belongs to.
    """

    _name = "channel.channex.channel"
    _description = "Channel Channex Channel"
    _rec_name = "title"
    _order = "title"

    backend_id = fields.Many2one(
        comodel_name="channel.channex.backend",
        required=True,
        readonly=True,
        ondelete="cascade",
    )
    external_id = fields.Char(
        string="Channex channel ID",
        required=True,
        readonly=True,
        help="What a booking carries, so it is what the partner is resolved by.",
    )
    title = fields.Char(readonly=True)
    code = fields.Char(
        string="Channel code",
        readonly=True,
        help="As Channex reports it, e.g. BookingCom.",
    )
    ota_id = fields.Many2one(
        comodel_name="channel.channex.ota",
        string="OTA",
        required=True,
        readonly=True,
        ondelete="restrict",
    )
    agency_id = fields.Many2one(
        related="ota_id.agency_id",
        readonly=False,
        string="Agency",
        help="Shared by every property: a channel code means the same partner "
        "everywhere. Set it here and the other properties inherit it.",
    )
    hotel_id = fields.Char(
        string="OTA hotel ID",
        readonly=True,
        help="The identifier of the hotel on the OTA side, to tell two channels "
        "of the same OTA apart.",
    )
    channex_active = fields.Boolean(
        string="Enabled on Channex",
        readonly=True,
        help="A disabled channel sends no bookings yet, but it will once the "
        "hotel enables it.",
    )
    active = fields.Boolean(
        default=True,
        readonly=True,
        help="Unticked when the channel is gone from Channex. The row stays so "
        "the partner mapping survives, and so old bookings still resolve.",
    )
    sync_date = fields.Datetime(string="Last seen", readonly=True)

    _sql_constraints = [
        (
            "external_uniq",
            "unique(backend_id, external_id)",
            "This channel is already known for this backend.",
        ),
    ]

    @api.model
    def _channex_upsert(self, backend, values):
        """Take in one channel as Channex reports it.

        Matched by its Channex id rather than by code or title: a hotel can
        connect the same OTA twice, and Channex generates the title.
        """
        record = self.with_context(active_test=False).search(
            [
                ("backend_id", "=", backend.id),
                ("external_id", "=", values["id"]),
            ]
        )
        data = {
            "title": values.get("title"),
            "code": values.get("channel"),
            # Channex keeps the OTA side hotel id inside settings.
            "hotel_id": (values.get("settings") or {}).get("hotel_id"),
            "channex_active": bool(values.get("is_active")),
            "active": True,
            "sync_date": fields.Datetime.now(),
        }
        if record:
            record.write(data)
            return record
        return self.create(
            {
                **data,
                "backend_id": backend.id,
                "external_id": values["id"],
                "ota_id": self.env["channel.channex.ota"]
                ._channex_of_code(values.get("channel"))
                .id,
            }
        )
