from odoo import _
from odoo.exceptions import MissingError

from odoo.addons.base_rest import restapi
from odoo.addons.base_rest_datamodel.restapi import Datamodel
from odoo.addons.component.core import Component

from ..pms_api_rest_utils import pms_api_check_access


class PmsBoardServiceService(Component):
    _inherit = "base.rest.service"
    _name = "pms.board.service.line.service"
    _usage = "board-service-lines"
    _collection = "pms.services"

    # The lines of a board service are its variants: one for each kind of
    # guest, or a single one for every guest

    def _board_service_line_info(self, product):
        PmsBoardServiceLineInfo = self.env.datamodels["pms.board.service.line.info"]
        return PmsBoardServiceLineInfo(
            id=product.id,
            name=product.product_tmpl_id.name,
            boardServiceId=product.product_tmpl_id.id,
            productId=product.id,
            amount=round(product.lst_price, 2),
        )

    @restapi.method(
        [
            (
                [
                    "/",
                ],
                "GET",
            )
        ],
        input_param=Datamodel("pms.board.service.line.search.param"),
        output_param=Datamodel("pms.board.service.line.info", is_list=True),
        auth="jwt_api_pms",
    )
    def get_board_service_lines(self, board_service_lines_search_param):
        room_only = self.env["product.template"]._room_only_board_service()
        domain = [
            ("is_board_service", "=", True),
            ("product_tmpl_id", "!=", room_only.id),
        ]
        if board_service_lines_search_param.boardServiceId:
            domain.append(
                (
                    "product_tmpl_id",
                    "=",
                    board_service_lines_search_param.boardServiceId,
                )
            )
        products = self.env["product.product"].sudo().search(domain)
        pms_api_check_access(user=self.env.user, records=products.product_tmpl_id)
        return [self._board_service_line_info(product) for product in products]

    @restapi.method(
        [
            (
                [
                    "/<int:board_service_line_id>",
                ],
                "GET",
            )
        ],
        output_param=Datamodel("pms.board.service.line.info", is_list=False),
        auth="jwt_api_pms",
    )
    def get_board_service_line(self, board_service_line_id):
        product = (
            self.env["product.product"]
            .sudo()
            .search(
                [("id", "=", board_service_line_id), ("is_board_service", "=", True)]
            )
        )
        if not product:
            raise MissingError(_("Board service line not found"))
        pms_api_check_access(user=self.env.user, records=product.product_tmpl_id)
        return self._board_service_line_info(product)
