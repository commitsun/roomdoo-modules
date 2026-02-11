import base64
from datetime import date
from typing import Annotated

from fastapi import Depends
from fastapi.responses import Response

from odoo import _, fields, models
from odoo.api import Environment
from odoo.exceptions import MissingError

from odoo.addons.fastapi_auth_jwt.dependencies import AuthJwtOdooEnv
from odoo.addons.pms_fastapi.models.fastapi_endpoint import pms_api_router
from odoo.addons.pms_fastapi.schemas.base import PmsBaseModel


@pms_api_router.get(
    "/reports/kelly-report",
    tags=["report"],
    responses={200: {"content": {"application/vnd.ms-excel": {}}}},
    response_class=Response,
)
async def kelly_report(
    env: Annotated[Environment, Depends(AuthJwtOdooEnv(validator_name="api_pms"))],
    pmsPropertyId: int,
    dateFrom: date,
) -> Response:
    helper = env["roomdoo.report_router.helper"].new()
    result = helper.generate_kelly_report(pmsPropertyId, dateFrom)
    return Response(
        content=base64.b64decode(result["xls_binary"]),
        media_type="application/vnd.ms-excel",
        headers={
            "Content-Disposition": f'attachment; filename="{result["xls_filename"]}"'
        },
    )


@pms_api_router.get(
    "/reports/ine-report",
    tags=["report"],
    responses={200: {"content": {"application/xml": {}}}},
    response_class=Response,
)
async def ine_report(
    env: Annotated[Environment, Depends(AuthJwtOdooEnv(validator_name="api_pms"))],
    pmsPropertyId: int,
    dateFrom: date,
    dateTo: date,
) -> Response:
    helper = env["roomdoo.report_router.helper"].new()
    result = helper.generate_ine_report(pmsPropertyId, dateFrom, dateTo)
    file_name = (
        "INE_" + dateFrom.strftime("%m") + "_" + dateFrom.strftime("%Y") + ".xml"
    )
    return Response(
        content=base64.b64decode(result),
        media_type="application/xml",
        headers={"Content-Disposition": f'attachment; filename="{file_name}"'},
    )


@pms_api_router.get(
    "/reports/transactions-report",
    tags=["report"],
    responses={200: {"content": {"application/vnd.ms-excel": {}}}},
    response_class=Response,
)
async def transactions_report(
    env: Annotated[Environment, Depends(AuthJwtOdooEnv(validator_name="api_pms"))],
    pmsPropertyId: int,
    dateFrom: date,
    dateTo: date,
) -> Response:
    helper = env["roomdoo.report_router.helper"].new()
    result = helper.generate_transactions_report(pmsPropertyId, dateFrom, dateTo)
    return Response(
        content=base64.b64decode(result["xls_binary"]),
        media_type="application/vnd.ms-excel",
        headers={
            "Content-Disposition": f'attachment; filename="{result["xls_filename"]}"'
        },
    )


@pms_api_router.get(
    "/reports/services-report",
    tags=["report"],
    responses={200: {"content": {"application/vnd.ms-excel": {}}}},
    response_class=Response,
)
async def services_report(
    env: Annotated[Environment, Depends(AuthJwtOdooEnv(validator_name="api_pms"))],
    pmsPropertyId: int,
    dateFrom: date,
    dateTo: date,
) -> Response:
    helper = env["roomdoo.report_router.helper"].new()
    result = helper.generate_sql_report(
        "pms_api_rest.sql_export_services",
        pmsPropertyId,
        date_from=dateFrom,
        date_to=dateTo,
    )
    return Response(
        content=base64.b64decode(result["binary"]),
        media_type="application/vnd.ms-excel",
        headers={
            "Content-Disposition": f'attachment; filename="{result["file_name"]}"'
        },
    )


@pms_api_router.get(
    "/reports/departures-report",
    tags=["report"],
    responses={200: {"content": {"application/vnd.ms-excel": {}}}},
    response_class=Response,
)
async def departures_report(
    env: Annotated[Environment, Depends(AuthJwtOdooEnv(validator_name="api_pms"))],
    pmsPropertyId: int,
    dateFrom: date,
) -> Response:
    helper = env["roomdoo.report_router.helper"].new()
    result = helper.generate_sql_report(
        "pms_api_rest.sql_export_departures",
        pmsPropertyId,
        date_from=dateFrom,
    )
    return Response(
        content=base64.b64decode(result["binary"]),
        media_type="application/vnd.ms-excel",
        headers={
            "Content-Disposition": f'attachment; filename="{result["file_name"]}"'
        },
    )


@pms_api_router.get(
    "/reports/arrivals-report",
    tags=["report"],
    responses={200: {"content": {"application/vnd.ms-excel": {}}}},
    response_class=Response,
)
async def arrivals_report(
    env: Annotated[Environment, Depends(AuthJwtOdooEnv(validator_name="api_pms"))],
    pmsPropertyId: int,
    dateFrom: date,
) -> Response:
    helper = env["roomdoo.report_router.helper"].new()
    result = helper.generate_sql_report(
        "pms_api_rest.sql_export_arrivals",
        pmsPropertyId,
        date_from=dateFrom,
    )
    return Response(
        content=base64.b64decode(result["binary"]),
        media_type="application/vnd.ms-excel",
        headers={
            "Content-Disposition": f'attachment; filename="{result["file_name"]}"'
        },
    )


# ============== BUSINESS LOGIC HELPER ==============


class RoomdooReportRouterHelper(models.AbstractModel):
    _name = "roomdoo.report_router.helper"
    _description = "Roomdoo Report Router Helper"

    def _check_property_access(self, pms_property_id):
        PmsBaseModel.pms_api_check_access(
            self.env.user,
            self.env["pms.property"].sudo().browse(pms_property_id),
        )

    def generate_kelly_report(self, pms_property_id, date_from):
        self._check_property_access(pms_property_id)
        report_wizard = (
            self.env["kellysreport"]
            .sudo()
            .create(
                {
                    "date_start": date_from,
                    "pms_property_id": pms_property_id,
                }
            )
        )
        report_wizard.calculate_report()
        return report_wizard._excel_export()

    def generate_ine_report(self, pms_property_id, date_from, date_to):
        self._check_property_access(pms_property_id)
        report_wizard = (
            self.env["pms.ine.wizard"]
            .sudo()
            .create(
                {
                    "start_date": date_from,
                    "end_date": date_to,
                    "pms_property_id": pms_property_id,
                }
            )
        )
        report_wizard.ine_generate_xml()
        return report_wizard.txt_binary

    def generate_transactions_report(self, pms_property_id, date_from, date_to):
        self._check_property_access(pms_property_id)
        report_wizard = (
            self.env["cash.daily.report.wizard"]
            .sudo()
            .create(
                {
                    "date_start": date_from,
                    "date_end": date_to,
                    "pms_property_id": pms_property_id,
                }
            )
        )
        return report_wizard._export(pms_property_id)

    def generate_sql_report(self, xml_id, pms_property_id, date_from, date_to=None):
        """Generate a report using sql.file.wizard with the given xml_id reference.

        Shared logic for services, departures, and arrivals reports.
        """
        self._check_property_access(pms_property_id)
        query = self.env.ref(xml_id).sudo()
        if not query:
            raise MissingError(_("SQL query not found"))
        report_wizard = (
            self.env["sql.file.wizard"].sudo().create({"sql_export_id": query.id})
        )
        params = {
            "x_date_from": fields.Date.to_string(date_from),
            "x_pms_property_id": pms_property_id,
        }
        if date_to:
            params["x_date_to"] = fields.Date.to_string(date_to)
        vals = []
        for item in report_wizard.query_properties:
            if item["string"] in params:
                vals.append({"name": item["name"], "value": params[item["string"]]})
        report_wizard.write({"query_properties": vals})
        report_wizard.export_sql()
        return {
            "file_name": report_wizard.file_name,
            "binary": report_wizard.binary_file,
        }
