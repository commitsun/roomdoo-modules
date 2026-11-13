# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import api, fields, models

from ..common.ari import union_window

ROOM_TYPE_BINDING = "channel.channex.pms.room.type"


class ChannelChannexInventoryCap(models.Model):
    """What an agency has set aside of a room type, as Channex is told it.

    pms caps what a room type sells through an agency, or through the sale
    channel the agency belongs to, apart from what it sells through any
    channel. Channex expresses that as availability rules on the channels the
    agency is mapped to: the OTA then sells the lowest of the room type's
    availability and the rule.

    One row per backend, agency and room type, which is what a change notes as
    pending. The rules themselves are not kept here: Channex is asked for them
    on every send, so a send that fails halfway never leaves behind rules
    nobody knows about.
    """

    _name = "channel.channex.inventory.cap"
    _inherit = "channel.channex.ari.mixin"
    _description = "Channel Channex inventory cap of an agency"

    backend_id = fields.Many2one(
        comodel_name="channel.channex.backend",
        required=True,
        ondelete="cascade",
        index=True,
    )
    agency_id = fields.Many2one(
        comodel_name="res.partner",
        string="Agency",
        required=True,
        ondelete="cascade",
    )
    room_type_id = fields.Many2one(
        comodel_name="pms.room.type",
        required=True,
        ondelete="cascade",
    )

    _sql_constraints = [
        (
            "cap_uniq",
            "unique(backend_id, agency_id, room_type_id)",
            "There is already a cap for this agency and room type.",
        ),
    ]

    def _channex_channels(self):
        """The Channex ids of the channels this agency sells through, here."""
        self.ensure_one()
        channels = self.env["channel.channex.channel"].search(
            [
                ("backend_id", "=", self.backend_id.id),
                ("agency_id", "=", self.agency_id.id),
            ]
        )
        return sorted(channels.mapped("external_id"))

    def _channex_room_type_binding(self):
        self.ensure_one()
        return self.env[ROOM_TYPE_BINDING].search(
            [
                ("backend_id", "=", self.backend_id.id),
                ("odoo_id", "=", self.room_type_id.id),
                ("external_id", "!=", False),
            ],
            limit=1,
        )

    def _channex_marker(self):
        """What tells our rules apart from the ones the hotel makes by hand on
        Channex, which are never touched. In the title, the one thing a rule
        carries that is ours to write."""
        self.ensure_one()
        return f"[roomdoo:{self.agency_id.id}:{self.room_type_id.id}]"

    def _channex_title(self, date_from, date_to):
        """Readable on Channex, and different for every rule of the same cap:
        the dates go in it as well as the marker."""
        self.ensure_one()
        return (
            f"{self.agency_id.name} · {self.room_type_id.name} · "
            f"{date_from} – {date_to} {self._channex_marker()}"
        )

    @api.model
    def _channex_ari_resolve(self, entries):
        """Caps are noted per ``(property_id, agency_id, room_type_id)``, any of
        them ``None`` to mean every one."""
        resolved = {}
        backends = self.env["channel.channex.backend"]
        for (property_id, agency_id, room_type_id), window in entries.items():
            domain = [("pms_property_id", "=", property_id)] if property_id else []
            for backend in backends.search(domain):
                for cap in self._channex_caps_of(backend, agency_id, room_type_id):
                    resolved[cap] = union_window(resolved.get(cap), window)
        return resolved

    @api.model
    def _channex_caps_of(self, backend, agency_id=None, room_type_id=None):
        """Every agency and room type this backend can have a cap for, made as
        needed.

        The agencies mapped to one of its channels, and also those it already
        has a cap for: an agency no longer mapped still has rules on Channex,
        and they have to be taken down.
        """
        existing = self.search([("backend_id", "=", backend.id)])
        agencies = (
            self.env["channel.channex.channel"]
            .search([("backend_id", "=", backend.id)])
            .agency_id
            | existing.agency_id
        )
        room_types = (
            self.env[ROOM_TYPE_BINDING]
            .search([("backend_id", "=", backend.id), ("external_id", "!=", False)])
            .odoo_id
            | existing.room_type_id
        )
        if agency_id:
            agencies = agencies.filtered(lambda agency: agency.id == agency_id)
        if room_type_id:
            room_types = room_types.filtered(lambda rt: rt.id == room_type_id)
        caps = self.browse()
        for agency in agencies:
            for room_type in room_types:
                cap = existing.filtered(
                    lambda c, a=agency, r=room_type: c.agency_id == a
                    and c.room_type_id == r
                )
                caps |= cap or self.create(
                    {
                        "backend_id": backend.id,
                        "agency_id": agency.id,
                        "room_type_id": room_type.id,
                    }
                )
        return caps
