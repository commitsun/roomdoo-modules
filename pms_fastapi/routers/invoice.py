from typing import Annotated

from fastapi import Depends, Query
from fastapi.responses import JSONResponse, Response

from odoo import api, models
from odoo.osv import expression

from odoo.addons.account.models.account_move import AccountMove
from odoo.addons.extendable_fastapi.schemas import PagedCollection
from odoo.addons.fastapi.dependencies import (
    paging,
)
from odoo.addons.fastapi.schemas import Paging
from odoo.addons.pms_fastapi.dependencies import (
    AuthenticatedEnv,
    create_order_dependency,
)
from odoo.addons.pms_fastapi.models.fastapi_endpoint import pms_api_router
from odoo.addons.pms_fastapi.schemas.invoice import (
    INVOICE_ORDER_MAPPING,
    InvoiceOrderField,
    InvoiceSearch,
    InvoiceSummary,
    ReportFormatEnum,
)
from odoo.addons.pms_fastapi.utils import FilteredModelAdapter

INVOICE_REPORT_MAX_RECORDS = 5000

InvoiceOrderDependency = create_order_dependency(
    InvoiceOrderField, INVOICE_ORDER_MAPPING, ["-invoice_date,-name"]
)


@pms_api_router.get(
    "/invoices",
    response_model=PagedCollection[InvoiceSummary],
    tags=["invoice"],
)
async def list_invoices(
    env: AuthenticatedEnv,
    filters: Annotated[InvoiceSearch, Depends()],
    paging: Annotated[Paging, Depends(paging)],
    orderBy: Annotated[str, Depends(InvoiceOrderDependency)],
) -> PagedCollection[InvoiceSummary]:
    """List invoices with pagination and filtering"""
    count, invoices = (
        env["pms_api_invoice.invoice_router.helper"]
        .new()
        ._search(paging, filters, orderBy)
    )
    return PagedCollection[InvoiceSummary](
        count=count,
        items=[InvoiceSummary.from_account_move(invoice) for invoice in invoices],
    )


@pms_api_router.get(
    "/invoices/extra-features", response_model=list[str], tags=["invoice"]
)
async def invoice_extra_features(
    env: AuthenticatedEnv,
) -> list[str]:
    return env["pms_api_invoice.invoice_router.helper"].extra_features()


@pms_api_router.post(
    "/invoices/report",
    tags=["invoice"],
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
async def invoices_report(
    env: AuthenticatedEnv,
    report_format: Annotated[
        ReportFormatEnum,
        Query(alias="format", description="Output file format."),
    ],
    filters: Annotated[InvoiceSearch, Depends()],
    ids: Annotated[
        list[int] | None,
        Query(
            description="Invoice IDs to include in the report. "
            "Mutually exclusive with filter parameters.",
        ),
    ] = None,
) -> Response:
    """Generate and download an invoice report in PDF or Excel format."""
    return (
        env["pms_api_invoice.invoice_router.helper"]
        .new()
        ._generate_report(report_format, filters, ids)
    )


class PmsApiInvoiceRouterHelper(models.AbstractModel):
    _name = "pms_api_invoice.invoice_router.helper"
    _description = "PMS API Invoice Router Helper"

    def _get_domain_adapter(self):
        return [("move_type", "in", ["out_invoice", "out_refund"])]

    def _get_multicompany_rule(self):
        return []

    @property
    def model_adapter(self) -> FilteredModelAdapter[AccountMove]:
        base_domain = self._get_domain_adapter()
        multicompany_domain = self._get_multicompany_rule()
        model_domain = expression.AND([base_domain, multicompany_domain])
        return FilteredModelAdapter[AccountMove](self.env, model_domain)

    def get(self, record_id) -> AccountMove:
        return self.model_adapter.get(record_id)

    def _search(self, paging, params, order) -> tuple[int, AccountMove]:
        return self.model_adapter.search_with_count(
            params.to_odoo_domain(self.env),
            limit=paging.limit,
            offset=paging.offset,
            order=order,
            context=params.to_odoo_context(self.env),
        )

    def count(self, params=None) -> int:
        if params:
            domain = params.to_odoo_domain(self.env)
        else:
            domain = []
        return self.model_adapter.count(domain)

    @api.model
    def extra_features(self):
        return []

    # -- Invoice report helpers --

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
        if count > INVOICE_REPORT_MAX_RECORDS:
            return JSONResponse(
                status_code=400,
                content={
                    "type": "/errors/record-limit-exceeded",
                    "title": "Record limit exceeded",
                    "status": 400,
                    "detail": (
                        f"The export requested {count} records, "
                        f"but the maximum allowed is {INVOICE_REPORT_MAX_RECORDS}."
                    ),
                    "requestedCount": count,
                    "maxAllowed": INVOICE_REPORT_MAX_RECORDS,
                },
                media_type="application/problem+json",
            )
        if ids:
            invoices = self.model_adapter.search(domain)
        else:
            invoices = self.model_adapter.search(domain, context=context)
        if report_format == ReportFormatEnum.xlsx:
            return self._render_invoice_xlsx(invoices)
        return self._render_invoice_pdf(invoices)

    def _get_invoice_xlsx_report_name(self):
        return "roomdoo_invoices_exporter.invoice_payment_report"

    def _render_invoice_xlsx(self, invoices):
        report_name = self._get_invoice_xlsx_report_name()
        content, _report_type = (
            self.env["ir.actions.report"].sudo()._render(report_name, invoices.ids)
        )
        return Response(
            content=content,
            media_type=(
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ),
            headers={
                "Content-Disposition": ('attachment; filename="invoices_report.xlsx"')
            },
        )

    def _render_invoice_pdf(self, invoices):
        return JSONResponse(
            status_code=501,
            content={
                "type": "/errors/not-implemented",
                "title": "Not implemented",
                "status": 501,
                "detail": "PDF report generation is not yet implemented.",
            },
            media_type="application/problem+json",
        )
