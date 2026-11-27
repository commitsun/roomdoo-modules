# Copyright 2021 Eric Antones <eantones@nuobit.com>
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import api, fields, models
from odoo.osv import expression

from ..pms_availability.listener import buffer_property_exports_for_rooms


class PmsReservation(models.Model):
    _inherit = "pms.reservation"

    channel_external_ids = fields.Char(
        string="External IDs",
        compute="_compute_channel_external_ids",
        search="_search_channel_external_ids",
    )

    def _compute_channel_external_ids(self):
        for record in self:
            # ``external_id`` is an Integer on this binding, so it has to be
            # cast before joining.
            record.channel_external_ids = ",".join(
                str(external_id)
                for external_id in record.folio_id.channel_wubook_bind_ids.mapped(
                    "external_id"
                )
            )

    def _search_channel_external_ids(self, operator, value):
        folios = self.env["pms.folio"].search(
            [("channel_wubook_bind_ids.external_id", operator, value)]
        )
        return [("folio_id", "in", folios.ids)]

    @api.model
    def _name_search(
        self, name="", args=None, operator="ilike", limit=100, name_get_uid=None
    ):
        args = args or []
        domain = []
        if name:
            domain = [
                "|",
                "|",
                ("name", operator, name),
                ("reservation_origin_code", operator, name),
                ("channel_wubook_bind_ids.external_id", operator, name),
            ]
        return self._search(
            expression.AND([domain + args]), limit=limit, access_rights_uid=name_get_uid
        )

    def _connector_publish_availability(self):
        """Stage the availability export of the nights of these reservations.

        A reservation can sit in a room of a different type than the one
        booked (cross-type relocation) and it is the ASSIGNED room that moves
        availability, so both room types are refreshed. Staging goes through
        the same precommit buffer as the listeners, so a burst of imports
        still collapses to one job per (backend × property) pair.
        """
        res = super()._connector_publish_availability()
        for pms_property in self.pms_property_id:
            reservations = self.filtered(
                lambda r, pms_property=pms_property: r.pms_property_id == pms_property
            )
            dates = reservations.reservation_line_ids.mapped("date")
            if not dates:
                continue
            room_types = (
                reservations.room_type_id
                | reservations.reservation_line_ids.room_id.room_type_id
            )
            buffer_property_exports_for_rooms(
                self.env, pms_property, room_types, min(dates), max(dates)
            )
        return res
