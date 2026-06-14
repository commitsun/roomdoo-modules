from datetime import datetime

from odoo import _
from odoo.exceptions import MissingError

from odoo.addons.base_rest import restapi
from odoo.addons.base_rest_datamodel.restapi import Datamodel
from odoo.addons.component.core import Component

from ..pms_api_rest_utils import pms_api_check_access


class PmsServiceLineService(Component):
    _inherit = "base.rest.service"
    _name = "pms.service.line.service"
    _usage = "service-lines"
    _collection = "pms.services"

    @restapi.method(
        [
            (
                [
                    "/<int:service_line_id>",
                ],
                "GET",
            )
        ],
        output_param=Datamodel("pms.service.line.info", is_list=False),
        auth="jwt_api_pms",
    )
    def get_service_line(self, service_line_id):
        service_line = self.env["pms.service.line"].sudo().browse(service_line_id)
        if not service_line.exists():
            raise MissingError(_("Service line not found"))
        pms_api_check_access(user=self.env.user, records=service_line)
        PmsServiceLineInfo = self.env.datamodels["pms.service.line.info"]
        company_currency = (
            True
            if service_line.currency_id == service_line.company_id.currency_id
            else False
        )
        price_unit = (
            service_line.price_unit
            if company_currency
            else service_line.currency_id._convert(
                service_line.price_unit,
                service_line.company_id.currency_id,
                service_line.company_id,
                service_line.date,
            )
        )
        discount = (
            service_line.discount
            if company_currency
            else service_line.currency_id._convert(
                service_line.discount,
                service_line.company_id.currency_id,
                service_line.company_id,
                service_line.date,
            )
        )
        return PmsServiceLineInfo(
            id=service_line.id,
            date=datetime.combine(service_line.date, datetime.min.time()).isoformat(),
            priceUnit=round(price_unit, 2),
            discount=round(discount, 2),
            quantity=service_line.day_qty,
        )

    @restapi.method(
        [
            (
                [
                    "/p/<int:service_line_id>",
                ],
                "PATCH",
            )
        ],
        input_param=Datamodel("pms.service.line.info"),
        auth="jwt_api_pms",
    )
    def update_service_line(self, service_line_id, pms_service_line_info_data):
        service_line = self.env["pms.service.line"].sudo().browse(service_line_id)
        if not service_line.exists():
            raise MissingError(_("Service line not found"))
        pms_api_check_access(user=self.env.user, records=service_line)
        vals = {}
        if service_line:
            if pms_service_line_info_data.date:
                vals["date"] = datetime.strptime(
                    pms_service_line_info_data.date, "%Y-%m-%d"
                ).date()
            if pms_service_line_info_data.discount is not None:
                vals["discount"] = pms_service_line_info_data.discount
            if pms_service_line_info_data.quantity is not None:
                vals["day_qty"] = pms_service_line_info_data.quantity
            if pms_service_line_info_data.priceUnit is not None:
                vals["price_unit"] = pms_service_line_info_data.priceUnit
            service_line.write(vals)
        else:
            raise MissingError(_("Service line not found"))

    @restapi.method(
        [
            (
                [
                    "/<int:service_line_id>",
                ],
                "DELETE",
            )
        ],
        auth="jwt_api_pms",
    )
    def delete_service_line(self, service_line_id):
        # esto tb podría ser con un browse
        service_line = self.env["pms.service.line"].sudo().browse(service_line_id)
        if not service_line.exists():
            raise MissingError(_("Service line not found"))
        pms_api_check_access(user=self.env.user, records=service_line)
        service_line.unlink()
