# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from odoo.addons.connector.exception import IDMissingInBackend

from ...components.adapter import ChannexAPIError
from ..common.ari import stage_ari, union_window


class ChannelChannexRatePlan(models.Model):
    """A pricelist, one of its board services and a room type, as Channex
    sells them: a rate plan.

    Not a binding: a binding stands for one Odoo record, and a pricelist is
    many rate plans. The Channex id is kept here, because it is what the
    channels are mapped to on Channex, and losing it would be making the rate
    plan again and losing what is mapped to it.
    """

    _name = "channel.channex.rate.plan"
    _inherit = "channel.channex.ari.mixin"
    _description = "Channel Channex rate plan"
    _order = "pricelist_id, board_service_id, room_type_binding_id"

    backend_id = fields.Many2one(
        comodel_name="channel.channex.backend",
        required=True,
        ondelete="cascade",
        index=True,
    )
    # A pricelist or a board service with rate plans on Channex cannot be
    # deleted before they are: their ids would be lost, and with them any way
    # of taking them down.
    pricelist_id = fields.Many2one(
        comodel_name="product.pricelist",
        required=True,
        ondelete="restrict",
        index=True,
    )
    board_service_id = fields.Many2one(
        comodel_name="product.template",
        string="Board service",
        required=True,
        ondelete="restrict",
    )
    # Channex takes the rate plans of a room type down with it.
    room_type_binding_id = fields.Many2one(
        comodel_name="channel.channex.pms.room.type",
        string="Room type",
        required=True,
        ondelete="cascade",
    )
    external_id = fields.Char(string="Channex ID", readonly=True, copy=False)
    channex_to_remove = fields.Boolean(
        string="Being removed",
        readonly=True,
        copy=False,
        help="No longer sold, and waiting to be taken down on Channex.",
    )

    _sql_constraints = [
        (
            "rate_plan_uniq",
            "unique(backend_id, pricelist_id, board_service_id, room_type_binding_id)",
            "There is already a rate plan for this pricelist, board service "
            "and room type.",
        ),
    ]

    def name_get(self):
        return [
            (
                record.id,
                f"{record._channex_title()} · {record.room_type_binding_id.name}",
            )
            for record in self
        ]

    @api.model
    def _channex_ari_resolve(self, entries):
        """Rates and restrictions are noted per ``(property_id, pricelist_id,
        room_type_id, board_service_id)``, any of them ``None`` to mean every
        one. Only rate plans on Channex and still sold count: one made later
        publishes its whole window as it is made."""
        resolved = {}
        for key, window in entries.items():
            property_id, pricelist_id, room_type_id, board_service_id = key
            domain = [("external_id", "!=", False), ("channex_to_remove", "=", False)]
            if property_id:
                domain.append(("backend_id.pms_property_id", "=", property_id))
            if pricelist_id:
                domain.append(("pricelist_id", "=", pricelist_id))
            if room_type_id:
                domain.append(("room_type_binding_id.odoo_id", "=", room_type_id))
            if board_service_id:
                domain.append(("board_service_id", "=", board_service_id))
            for rate_plan in self.search(domain):
                resolved[rate_plan] = union_window(resolved.get(rate_plan), window)
        return resolved

    def _channex_stage_restrictions(self):
        """Publish the whole window of these rate plans."""
        for record in self:
            stage_ari(
                self.env,
                "restrictions",
                (
                    record.backend_id.pms_property_id.id,
                    record.pricelist_id.id,
                    record.room_type_binding_id.odoo_id.id,
                    record.board_service_id.id,
                ),
            )

    def _channex_title(self):
        """In the language of the hotel, and not of whoever triggered the send:
        the title is also how a rate plan made by hand is recognised."""
        self.ensure_one()
        lang = self.backend_id.pms_property_id.lang
        pricelist = self.pricelist_id.with_context(lang=lang)
        board_service = self.board_service_id.with_context(lang=lang)
        return f"{pricelist.name} – {board_service.name}"

    def _channex_values(self):
        """What the rate plan is made of on Channex.

        Priced per guest and by hand, never derived by Channex: Odoo works out
        the price of every occupancy itself. The occupancies are only sent on
        creation, as from then on Channex makes them follow the room type.
        """
        self.ensure_one()
        room_type_binding = self.room_type_binding_id
        room_type = room_type_binding.odoo_id
        occupancies = room_type_binding.occ_adults
        primary = room_type.default_occupancy
        if not 0 < primary <= occupancies:
            primary = occupancies
        children_fee = room_type._get_occupancy_rule(self.pricelist_id).children_fee
        return {
            "title": self._channex_title(),
            "property_id": self.backend_id._channex_bound_property_id(),
            "room_type_id": room_type_binding.external_id,
            "currency": self.pricelist_id.currency_id.name,
            "sell_mode": "per_person",
            "rate_mode": "manual",
            "meal_type": self.board_service_id.channex_meal_type or "none",
            "children_fee": f"{children_fee:.2f}",
            "options": [
                {"occupancy": occupancy, "is_primary": occupancy == primary, "rate": 0}
                for occupancy in range(1, occupancies + 1)
            ],
        }

    def channex_remove(self):
        """Take this rate plan down on Channex. Job entry point.

        On its own job, so that a rate plan Channex will not remove leaves the
        rest of the rate plans alone, and this job failed for someone to see.
        Channex will not remove one mapped to a channel, and it is not forced
        to: that would unmap it without anyone on the channel knowing.
        """
        for record in self.exists():
            # Sold again since it was queued.
            if not record.channex_to_remove:
                continue
            with record.backend_id.work_on(self._name) as work:
                adapter = work.component(usage="backend.adapter")
                try:
                    adapter.delete(record.external_id)
                except IDMissingInBackend:
                    # Already gone, which is what was wanted.
                    pass
                except ChannexAPIError as error:
                    raise UserError(
                        _(
                            "Channex would not remove the rate plan %(rate_plan)s: "
                            "%(error)s\nIf a channel is mapped to it, unmap it on "
                            "Channex and run this job again."
                        )
                        % {"rate_plan": record.display_name, "error": error}
                    ) from error
            record.unlink()
        return True
