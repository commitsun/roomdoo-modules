from odoo import _
from odoo.exceptions import MissingError

from odoo.addons.base_rest import restapi
from odoo.addons.base_rest_datamodel.restapi import Datamodel
from odoo.addons.component.core import Component

from ..pms_api_rest_utils import pms_api_check_access


class PmsBoardServiceService(Component):
    _inherit = "base.rest.service"
    _name = "pms.board.service.service"
    _usage = "board-services"
    _collection = "pms.services"

    def _board_service_info(self, board_service, room_type_id=None, default=None):
        PmsBoardServiceInfo = self.env.datamodels["pms.board.service.info"]
        return PmsBoardServiceInfo(
            id=board_service.id,
            name=board_service.name,
            roomTypeId=room_type_id,
            amount=round(board_service.list_price, 2),
            boardServiceId=board_service.id,
            productIds=board_service.product_variant_ids.ids,
            boardServiceLineIds=board_service.product_variant_ids.ids,
            isDefaultBoardService=board_service == default,
            pricelistIds=self.env["product.pricelist"]
            .sudo()
            .search([("board_service_ids", "in", board_service.id)])
            .ids,
        )

    def _get_board_service(self, board_service_id):
        board_service = (
            self.env["product.template"]
            .sudo()
            .search([("id", "=", board_service_id), ("is_board_service", "=", True)])
        )
        if not board_service:
            raise MissingError(_("Board Service not found"))
        pms_api_check_access(user=self.env.user, records=board_service)
        return board_service

    @restapi.method(
        [
            (
                [
                    "/",
                ],
                "GET",
            )
        ],
        input_param=Datamodel("pms.board.service.search.param"),
        output_param=Datamodel("pms.board.service.info", is_list=True),
        auth="jwt_api_pms",
    )
    def get_board_services(self, board_services_search_param):
        external_app = self.env.user.pms_api_client
        room_only = self.env["product.template"]._room_only_board_service()
        domain = [("is_board_service", "=", True), ("id", "!=", room_only.id)]
        if board_services_search_param.name:
            domain.append(("name", "like", board_services_search_param.name))
        pms_property = self.env["pms.property"]
        if board_services_search_param.pmsPropertyId:
            pms_property = pms_property.sudo().browse(
                board_services_search_param.pmsPropertyId
            )
            domain.extend(
                [
                    "|",
                    ("pms_property_ids", "=", False),
                    ("pms_property_ids", "in", pms_property.id),
                ]
            )

        board_services = self.env["product.template"].sudo().search(domain)
        pms_api_check_access(user=self.env.user, records=board_services)
        # Board services are no longer bound to room types: they are listed
        # once for each room type asked for, or for each one of the property
        room_type_ids = (
            [board_services_search_param.roomTypeId]
            if board_services_search_param.roomTypeId
            else self.env["pms.room"]
            .sudo()
            .search([("pms_property_id", "=", pms_property.id)])
            .mapped("room_type_id.id")
        )
        default = pms_property.default_pricelist_id.default_board_service_id
        result_board_services = [
            self._board_service_info(board_service, room_type_id, default)
            for room_type_id in room_type_ids
            for board_service in board_services
        ]
        if external_app:
            pms_api_check_access(
                user=self.env.user,
                records=self.env["pms.room.type"].sudo().browse(room_type_ids),
            )
            PmsBoardServiceInfo = self.env.datamodels["pms.board.service.info"]
            for room_type_id in room_type_ids:
                result_board_services.append(
                    PmsBoardServiceInfo(
                        id=0,
                        name="Solo Alojamiento",
                        roomTypeId=room_type_id,
                        amount=0,
                        boardServiceId=0,
                        boardServiceLineIds=[],
                    )
                )
        return result_board_services

    @restapi.method(
        [
            (
                [
                    "/<int:board_service_id>",
                ],
                "GET",
            )
        ],
        output_param=Datamodel("pms.board.service.info", is_list=False),
        auth="jwt_api_pms",
    )
    def get_board_service(self, board_service_id):
        return self._board_service_info(self._get_board_service(board_service_id))

    @restapi.method(
        [
            (
                [
                    "/<int:board_service_id>/lines",
                ],
                "GET",
            )
        ],
        input_param=Datamodel("pms.search.param"),
        output_param=Datamodel("pms.board.service.line.info", is_list=True),
        auth="jwt_api_pms",
    )
    def get_board_service_lines(self, board_service_id, pms_search_param):
        board_service = self._get_board_service(board_service_id)
        if (
            pms_search_param.pmsPropertyId
            and board_service.pms_property_ids
            and pms_search_param.pmsPropertyId not in board_service.pms_property_ids.ids
        ):
            return []
        PmsBoardServiceLineInfo = self.env.datamodels["pms.board.service.line.info"]
        return [
            PmsBoardServiceLineInfo(
                id=product.id,
                name=board_service.name,
                boardServiceId=board_service.id,
                productId=product.id,
                amount=round(product.lst_price, 2),
                isAdults=product.board_guest_type in ("adults", "all"),
                isChildren=product.board_guest_type in ("children", "all"),
            )
            for product in board_service.product_variant_ids
        ]

    @restapi.method(
        [
            (
                [
                    "/restricted/<int:board_service_id>",
                ],
                "GET",
            )
        ],
        output_param=Datamodel("pms.board.service.info", is_list=False),
        auth="jwt_api_pms",
    )
    def get_restricted_board_service(self, board_service_id):
        board_service = (
            self.env["product.template"]
            .sudo()
            .search([("id", "=", board_service_id), ("is_board_service", "=", True)])
        )
        if not board_service:
            raise MissingError(_("Board Service not found"))
        PmsBoardServiceInfo = self.env.datamodels["pms.board.service.info"]
        return PmsBoardServiceInfo(
            id=board_service.id,
            name=board_service.name,
            amount=round(board_service.list_price),
            productIds=board_service.product_variant_ids.ids,
        )
