# Copyright 2021 Eric Antones <eantones@nuobit.com>
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import api, fields, models


class PmsRoomType(models.Model):
    _name = "pms.room.type"
    _inherit = ["pms.room.type", "channel.wubook.connect.mixin"]

    channel_wubook_bind_ids = fields.One2many(
        comodel_name="channel.wubook.pms.room.type",
        inverse_name="odoo_id",
        string="Channel Wubook PMS Bindings",
    )

    @api.model
    def _has_price_derivation(self):
        """Whether ``pms`` can derive the price of a room type from another
        one (``price_base_room_type_id``).

        The connector may be updated before ``pms``: until then no room type
        derives its price, so there are no derived prices to push.
        """
        return "price_base_room_type_id" in self._fields

    def _get_wubook_board_service_amount(self, pms_property, pricelist_id=False):
        """Amount of the default board service in the Wubook prices.

        Wubook does not separate the price of the room type from the price
        of its default board service: the prices pushed to Wubook add the
        amount of the default board service of the room type in the
        property, if any, for every guest of its capacity.

        :param pricelist_id: id of the pricelist the default board service
            may be restricted to (see ``get_default_board_service``)
        """
        self.ensure_one()
        default_board_service = self.get_default_board_service(
            pms_property_id=pms_property.id,
            pricelist_id=pricelist_id,
            room_type_id=self.id,
        )
        if not default_board_service:
            return 0.0
        # TODO: get board service price with date context
        # (view _get_price_unit_line method in pms_service.py)
        return default_board_service.amount * self.get_room_type_capacity(
            pms_property_id=pms_property.id
        )

    # def get_binding(self, backend):
    #     self.ensure_one()
    #     return self.channel_wubook_bind_ids.filtered(
    #         lambda x: x.backend_id == backend
    #     )
