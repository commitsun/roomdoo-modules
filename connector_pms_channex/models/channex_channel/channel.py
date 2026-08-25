# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import api, fields, models

# Odoo names the four rate logics plainly; Channex prefixes them with "by".
CHANNEX_RATE_LOGIC = {
    "increase_amount": "increase_by_amount",
    "decrease_amount": "decrease_by_amount",
    "increase_percent": "increase_by_percent",
    "decrease_percent": "decrease_by_percent",
}


def _channex_rate_value(value):
    """Channex takes the amount as a string and repeats it back as given."""
    whole, _sep, fraction = ("%.2f" % value).partition(".")
    fraction = fraction.rstrip("0")
    return f"{whole}.{fraction}" if fraction else whole


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
    ota_price_modifier_type = fields.Selection(
        related="agency_id.ota_price_modifier_type",
        string="Rate Logic",
        readonly=True,
        help="Set on the agency, because every channel manager reads the same "
        "one. Shown here so the push is not a blind button.",
    )
    ota_price_modifier_value = fields.Float(
        related="agency_id.ota_price_modifier_value",
        string="Rate Value",
        readonly=True,
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

    # -- price modifier ----------------------------------------------------

    def action_push_price_modifier(self):
        """Publish the agency's rate logic into the mappings of these channels.

        Deliberate, never a listener: the mappings belong to the hotel, which
        builds them on the Channex side, and Odoo only reaches in to set one
        key of them. That is worth a button and a log entry.
        """
        for channel in self:
            channel._channex_push_price_modifier()
        return True

    def _channex_push_price_modifier(self):
        """Read the mappings, set or clear ``derived_option``, write them back.

        Read-modify-write is not an optimisation here, it is correctness: the
        rest of each mapping is the hotel's room and rate codes, and rebuilding
        the array from Odoo would wipe them.
        """
        self.ensure_one()
        if not self.agency_id:
            # No agency, no rate logic to speak of, and nothing of ours to clear.
            return False
        with self.backend_id.work_on(self._name) as work:
            adapter = work.component(usage="backend.adapter")
            mappings = (adapter.read(self.external_id) or {}).get("rate_plans") or []
            if not mappings:
                # The hotel has not mapped this channel yet: nowhere to put it.
                return False
            updated = [self._channex_mapping_with_rate_logic(m) for m in mappings]
            if updated == mappings:
                return False
            adapter.write(self.external_id, {"rate_plans": updated})
        return True

    def _channex_mapping_with_rate_logic(self, mapping):
        """One mapping, with our key set or removed and everything else intact."""
        self.ensure_one()
        settings = dict(mapping.get("settings") or {})
        modifier_type = self.agency_id.ota_price_modifier_type
        if modifier_type:
            settings["derived_option"] = {
                "rate": [
                    [
                        CHANNEX_RATE_LOGIC[modifier_type],
                        _channex_rate_value(self.agency_id.ota_price_modifier_value),
                    ]
                ]
            }
        else:
            # Cleared in Odoo means cleared on Channex, not quietly left behind.
            settings.pop("derived_option", None)
        return {**mapping, "settings": settings}
