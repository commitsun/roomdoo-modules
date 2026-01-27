import base64
from datetime import date
from typing import Annotated

from fastapi import Depends
from fastapi.responses import Response

from odoo import _, fields
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
    report_wizard = (
        env["kellysreport"]
        .sudo()
        .create(
            {
                "date_start": dateFrom,
                "pms_property_id": pmsPropertyId,
            }
        )
    )
    report_wizard.calculate_report()
    result = report_wizard._excel_export()
    file_name = result["xls_filename"]
    base64EncodedStr = result["xls_binary"]
    return Response(
        content=base64.b64decode(base64EncodedStr),
        media_type="application/vnd.ms-excel",
        headers={"Content-Disposition": f'attachment; filename="{file_name}"'},
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
    PmsBaseModel.pms_api_check_access(
        env.user,
        env["pms.property"].sudo().browse(pmsPropertyId),
    )
    report_wizard = (
        env["pms.ine.wizard"]
        .sudo()
        .create(
            {
                "start_date": dateFrom,
                "end_date": dateTo,
                "pms_property_id": pmsPropertyId,
            }
        )
    )
    report_wizard.ine_generate_xml()
    # file_name is INE_<date_from_MONTH>_<date_from_YEAR>.xml
    file_name = (
        "INE_" + dateFrom.strftime("%m") + "_" + dateFrom.strftime("%Y") + ".xml"
    )
    return Response(
        content=base64.b64decode(report_wizard.txt_binary),
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
    PmsBaseModel.pms_api_check_access(
        env.user,
        env["pms.property"].sudo().browse(pmsPropertyId),
    )

    report_wizard = (
        env["cash.daily.report.wizard"]
        .sudo()
        .create(
            {
                "date_start": dateFrom,
                "date_end": dateTo,
                "pms_property_id": pmsPropertyId,
            }
        )
    )
    result = report_wizard._export(pmsPropertyId)
    file_name = result["xls_filename"]
    base64EncodedStr = result["xls_binary"]
    return Response(
        content=base64.b64decode(base64EncodedStr),
        media_type="application/vnd.ms-excel",
        headers={"Content-Disposition": f'attachment; filename="{file_name}"'},
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
    PmsBaseModel.pms_api_check_access(
        env.user,
        env["pms.property"].sudo().browse(pmsPropertyId),
    )
    query = env.ref("pms_api_rest.sql_export_services").sudo()
    if not query:
        raise MissingError(_("SQL query not found"))
    report_wizard = env["sql.file.wizard"].sudo().create({"sql_export_id": query.id})
    charge_params = {
        "x_date_from": fields.Date.to_string(dateFrom),
        "x_date_to": fields.Date.to_string(dateTo),
        "x_pms_property_id": pmsPropertyId,
    }
    vals = []
    for item in report_wizard.query_properties:
        if item["string"] in charge_params:
            vals.append({"name": item["name"], "value": charge_params[item["string"]]})

    report_wizard.write({"query_properties": vals})
    report_wizard.export_sql()
    file_name = report_wizard.file_name
    base64EncodedStr = report_wizard.binary_file
    return Response(
        content=base64.b64decode(base64EncodedStr),
        media_type="application/vnd.ms-excel",
        headers={"Content-Disposition": f'attachment; filename="{file_name}"'},
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
    PmsBaseModel.pms_api_check_access(
        env.user,
        env["pms.property"].sudo().browse(pmsPropertyId),
    )
    query = env.ref("pms_api_rest.sql_export_departures").sudo()
    if not query:
        raise MissingError(_("SQL query not found"))
    report_wizard = env["sql.file.wizard"].sudo().create({"sql_export_id": query.id})
    charge_params = {
        "x_date_from": fields.Date.to_string(dateFrom),
        "x_pms_property_id": pmsPropertyId,
    }
    vals = []
    for item in report_wizard.query_properties:
        if item["string"] in charge_params:
            vals.append({"name": item["name"], "value": charge_params[item["string"]]})

    report_wizard.write({"query_properties": vals})
    report_wizard.export_sql()
    file_name = report_wizard.file_name
    base64EncodedStr = report_wizard.binary_file
    return Response(
        content=base64.b64decode(base64EncodedStr),
        media_type="application/vnd.ms-excel",
        headers={"Content-Disposition": f'attachment; filename="{file_name}"'},
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
    PmsBaseModel.pms_api_check_access(
        env.user,
        env["pms.property"].sudo().browse(pmsPropertyId),
    )
    query = env.ref("pms_api_rest.sql_export_arrivals").sudo()
    if not query:
        raise MissingError(_("SQL query not found"))
    report_wizard = env["sql.file.wizard"].sudo().create({"sql_export_id": query.id})
    charge_params = {
        "x_date_from": fields.Date.to_string(dateFrom),
        "x_pms_property_id": pmsPropertyId,
    }
    vals = []
    for item in report_wizard.query_properties:
        if item["string"] in charge_params:
            vals.append({"name": item["name"], "value": charge_params[item["string"]]})

    report_wizard.write({"query_properties": vals})
    report_wizard.export_sql()
    file_name = report_wizard.file_name
    base64EncodedStr = report_wizard.binary_file
    return Response(
        content=base64.b64decode(base64EncodedStr),
        media_type="application/vnd.ms-excel",
        headers={"Content-Disposition": f'attachment; filename="{file_name}"'},
    )
