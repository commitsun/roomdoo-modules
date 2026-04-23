from typing import Annotated

from fastapi import Depends, Query
from fastapi.responses import JSONResponse, Response

from odoo import api, models
from odoo.osv import expression

from odoo.addons.extendable_fastapi.schemas import PagedCollection
from odoo.addons.fastapi.dependencies import (
    paging,
)
from odoo.addons.fastapi.schemas import Paging
from odoo.addons.pms.models.pms_folio import PmsFolio
from odoo.addons.pms_fastapi.dependencies import (
    AuthenticatedEnv,
    create_order_dependency,
)
from odoo.addons.pms_fastapi.models.fastapi_endpoint import pms_api_router
from odoo.addons.pms_fastapi.schemas.pms_folio import (
    FOLIO_ORDER_MAPPING,
    FolioCountSummary,
    FolioOrderField,
    FolioSearch,
    FolioSummary,
    ReportFormatEnum,
)
from odoo.addons.pms_fastapi.utils import FilteredModelAdapter

FOLIO_REPORT_MAX_RECORDS = 5000

folio_order = create_order_dependency(
    FolioOrderField, FOLIO_ORDER_MAPPING, ["creationDate"]
)


@pms_api_router.post(
    "/folios/report",
    tags=["folio"],
    responses={
        200: {
            "content": {
                "application/pdf": {},
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {},
            }
        },
    },
    response_class=Response,
)
async def folios_report(
    env: AuthenticatedEnv,
    report_format: Annotated[
        ReportFormatEnum,
        Query(alias="format", description="Output file format."),
    ],
    filters: Annotated[FolioSearch, Depends()],
    ids: Annotated[
        list[int] | None,
        Query(
            description="Folio IDs to include in the report. "
            "Mutually exclusive with filter parameters.",
        ),
    ] = None,
) -> Response:
    """Generate and download a folio report in PDF or Excel format."""
    return (
        env["pms_api_folio.folio_router.helper"]
        .new()
        ._generate_report(report_format, filters, ids)
    )


@pms_api_router.get(
    "/folios/count",
    response_model=FolioCountSummary,
    tags=["folio"],
)
async def count_folios(
    env: AuthenticatedEnv,
    filters: Annotated[FolioSearch, Depends()],
) -> FolioCountSummary:
    """Get the counts of folios and their reservations matching the given filters"""
    folios_count, reservations_count = (
        env["pms_api_folio.folio_router.helper"].new().count(filters)
    )
    return FolioCountSummary(
        foliosCount=folios_count,
        reservationsCount=reservations_count,
    )


@pms_api_router.get(
    "/folios",
    response_model=PagedCollection[FolioSummary],
    tags=["folio"],
)
async def list_folios(
    env: AuthenticatedEnv,
    filters: Annotated[FolioSearch, Depends()],
    paging: Annotated[Paging, Depends(paging)],
    order: Annotated[str, Depends(folio_order)],
) -> PagedCollection[FolioSummary]:
    """Get the list of the folios"""
    count, folios = (
        env["pms_api_folio.folio_router.helper"]
        .new()
        ._search(paging, filters, order=order)
    )

    return PagedCollection[FolioSummary](
        count=count,
        items=[FolioSummary.from_pms_folio(folio) for folio in folios],
    )


class PmsApiFolioRouterHelper(models.AbstractModel):
    _name = "pms_api_folio.folio_router.helper"
    _description = "Pms api folio Service Helper"

    def _get_domain_adapter(self):
        return [("reservation_type", "!=", "out")]

    def _get_multicompany_rule(self):
        allowed_company_ids = self.env.user.company_ids.ids
        company_domain = expression.OR(
            [
                [("company_id", "=", False)],
                [("company_id", "in", allowed_company_ids)],
            ]
        )
        return company_domain

    @property
    def model_adapter(self) -> FilteredModelAdapter[PmsFolio]:
        base_domain = self._get_domain_adapter()
        multicompany_domain = self._get_multicompany_rule()
        model_domain = expression.AND([base_domain, multicompany_domain])
        return FilteredModelAdapter[PmsFolio](self.env, model_domain)

    def get(self, record_id) -> PmsFolio:
        return self.model_adapter.get(record_id)

    def _search(self, paging, params, order) -> tuple[int, PmsFolio]:
        return self.model_adapter.search_with_count(
            params.to_odoo_domain(self.env),
            limit=paging.limit,
            offset=paging.offset,
            context=params.to_odoo_context(self.env),
            order=order,
        )

    def count(self, params=None) -> tuple[int, int]:
        if params:
            domain = params.to_odoo_domain(self.env)
            context = params.to_odoo_context(self.env)
        else:
            domain = []
            context = {}
        # Resolve the folio domain once as a materialized CTE and reuse
        # it for both counts, so the subquery (which may include a large
        # id IN (...) coming from reservation pre-filtering) runs a
        # single time.
        full_folio_domain = expression.AND([self.model_adapter._base_domain, domain])
        folio_model = self.env["pms.folio"].sudo().with_context(**context)
        folio_query = folio_model._where_calc(full_folio_domain)
        folio_model._apply_ir_rules(folio_query, "read")
        folio_subselect, folio_params = folio_query.subselect()
        self.env.cr.execute(
            f"""
            WITH folio_ids AS MATERIALIZED ({folio_subselect})
            SELECT
                (SELECT COUNT(*) FROM folio_ids) AS folios,
                (SELECT COUNT(*) FROM pms_reservation r
                 WHERE r.folio_id IN (SELECT id FROM folio_ids)
                   AND (r.cancelled_reason IS NULL
                        OR r.cancelled_reason != 'modified')
                ) AS reservations
            """,
            folio_params,
        )
        folios_count, reservations_count = self.env.cr.fetchone()
        return folios_count, reservations_count

    @api.model
    def extra_features(self):
        return []

    # -- Folio report helpers --

    @staticmethod
    def _has_report_filters(filters):
        return any(v is not None for v in filters.__dict__.values())

    def _generate_report(self, report_format, filters, ids):
        if ids and self._has_report_filters(filters):
            return JSONResponse(
                status_code=400,
                content={
                    "type": "/errors/mutually-exclusive-params",
                    "title": "Mutually exclusive parameters",
                    "status": 400,
                    "detail": (
                        "Cannot specify both 'ids' and filter parameters. "
                        "Use one or the other."
                    ),
                },
                media_type="application/problem+json",
            )
        if ids:
            domain = [("id", "in", ids)]
            count = self.model_adapter.count(domain)
        else:
            domain = filters.to_odoo_domain(self.env)
            context = filters.to_odoo_context(self.env)
            count = self.model_adapter.count(domain, context=context)
        if count > FOLIO_REPORT_MAX_RECORDS:
            return JSONResponse(
                status_code=400,
                content={
                    "type": "/errors/record-limit-exceeded",
                    "title": "Record limit exceeded",
                    "status": 400,
                    "detail": (
                        f"The export requested {count} records, "
                        f"but the maximum allowed is {FOLIO_REPORT_MAX_RECORDS}."
                    ),
                    "requestedCount": count,
                    "maxAllowed": FOLIO_REPORT_MAX_RECORDS,
                },
                media_type="application/problem+json",
            )
        if ids:
            folios = self.model_adapter.search(domain)
        else:
            folios = self.model_adapter.search(domain, context=context)
        if report_format == ReportFormatEnum.xlsx:
            return self._render_folio_xlsx(folios)
        return self._render_folio_pdf(folios)

    def _get_folio_xlsx_report_name(self):
        return "pms_folio_report.folio_report_xlsx"

    def _get_folio_pdf_report_name(self):
        return "pms_folio_report.folio_summary_pdf"

    def _render_folio_xlsx(self, folios):
        report_name = self._get_folio_xlsx_report_name()
        content, _report_type = (
            self.env["ir.actions.report"].sudo()._render(report_name, folios.ids)
        )
        return Response(
            content=content,
            media_type=(
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ),
            headers={
                "Content-Disposition": 'attachment; filename="folios_report.xlsx"',
            },
        )

    def _render_folio_pdf(self, folios):
        report_name = self._get_folio_pdf_report_name()
        content, _report_type = (
            self.env["ir.actions.report"].sudo()._render(report_name, folios.ids)
        )
        return Response(
            content=content,
            media_type="application/pdf",
            headers={
                "Content-Disposition": 'attachment; filename="folios_report.pdf"',
            },
        )
