# Copyright 2021 Eric Antones <eantones@nuobit.com>
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).


from odoo import _
from odoo.exceptions import ValidationError

from odoo.addons.component.core import Component


class ChannelWubookPmsBoardServiceDelayedBatchImporter(Component):
    _name = "channel.wubook.pms.board.service.delayed.batch.importer"
    _inherit = "channel.wubook.delayed.batch.importer"

    _apply_on = "channel.wubook.pms.board.service"


class ChannelWubookPmsBoardServiceDirectBatchImporter(Component):
    _name = "channel.wubook.pms.board.service.direct.batch.importer"
    _inherit = "channel.wubook.direct.batch.importer"

    _apply_on = "channel.wubook.pms.board.service"


class ChannelWubookPmsBoardServiceImporter(Component):
    _name = "channel.wubook.pms.board.service.importer"
    _inherit = "channel.wubook.importer"

    _apply_on = "channel.wubook.pms.board.service"

    def _create(self, model, values):
        # A board service is a product with its variants and prices, so one
        # Wubook sells and no board service matches is bound by hand instead
        # of creating another one no pricelist sells
        raise ValidationError(
            _(
                "No board service has the internal reference %(code)s of the "
                "Wubook board %(name)s: bind it to its board service by hand, on "
                "the Connectors page of the board service"
            )
            % {"code": values.get("default_code"), "name": values.get("name")}
        )
