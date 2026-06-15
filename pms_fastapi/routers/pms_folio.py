from typing import Annotated

from fastapi import Depends, HTTPException, Query
from fastapi.responses import JSONResponse, Response

from odoo import api, models
from odoo.exceptions import AccessDenied, AccessError, MissingError, UserError
from odoo.osv import expression

from odoo.addons.extendable_fastapi.schemas import PagedCollection
from odoo.addons.fastapi.dependencies import (
    paging,
)
from odoo.addons.fastapi.schemas import Paging
from odoo.addons.pms.models.folio_sale_line import (
    FolioSaleLine as FolioSaleLineModel,
)
from odoo.addons.pms.models.pms_folio import PmsFolio
from odoo.addons.pms_fastapi.dependencies import (
    AuthenticatedEnv,
    create_order_dependency,
)
from odoo.addons.pms_fastapi.models.fastapi_endpoint import pms_api_router
from odoo.addons.pms_fastapi.models.folio_sale_line import (
    FOLIO_INVOICE_LINE_DESCRIPTIONS_CTX,
)
from odoo.addons.pms_fastapi.schemas.base import PmsBaseModel
from odoo.addons.pms_fastapi.schemas.contact import ContactIdImageEmail
from odoo.addons.pms_fastapi.schemas.folio_sale_line import FolioSaleLine
from odoo.addons.pms_fastapi.schemas.invoice import (
    FolioInvoiceCreate,
    InvoiceSummary,
)
from odoo.addons.pms_fastapi.schemas.pms_folio import (
    FOLIO_ORDER_MAPPING,
    FolioCountSummary,
    FolioDetail,
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


class _InvoiceCreationProblem(Exception):
    """Control-flow exception carrying an RFC 9457 JSONResponse."""

    def __init__(self, response):
        super().__init__()
        self.response = response


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


@pms_api_router.post(
    "/folios/invoices",
    response_model=InvoiceSummary,
    status_code=201,
    tags=["folio"],
)
async def create_folio_invoice(
    env: AuthenticatedEnv,
    payload: FolioInvoiceCreate,
) -> InvoiceSummary:
    """Create an invoice from folio sale lines.

    Supports grouping lines from multiple folios of the same property.
    Optionally validates (posts) the invoice on creation.
    """
    return env["pms_api_folio.folio_router.helper"].new()._create_invoice(payload)


@pms_api_router.get(
    "/folios/{folio_id}",
    response_model=FolioDetail,
    tags=["folio"],
)
async def get_folio(
    env: AuthenticatedEnv,
    folio_id: int,
) -> FolioDetail:
    """Get the billing detail of a folio.

    Includes totals and reservations with sale-line IDs.
    """
    helper = env["pms_api_folio.folio_router.helper"].new()
    try:
        folio = helper.get(folio_id)
    except MissingError as err:
        raise HTTPException(
            status_code=404,
            detail="folio not found",
        ) from err
    return FolioDetail.from_pms_folio(folio)


@pms_api_router.get(
    "/folios/{folio_id}/sale-lines",
    response_model=list[FolioSaleLine],
    tags=["folio"],
)
async def get_folio_sale_lines(
    env: AuthenticatedEnv,
    folio_id: int,
) -> list[FolioSaleLine]:
    """Get the billable sale lines of a folio with invoice state and tax breakdown."""
    helper = env["pms_api_folio.folio_router.helper"].new()
    try:
        folio = helper.get(folio_id)
    except MissingError as err:
        raise HTTPException(
            status_code=404,
            detail="folio not found",
        ) from err
    return helper.get_sale_lines(folio)


@pms_api_router.get(
    "/sale-lines/{line_id}",
    response_model=FolioSaleLine,
    tags=["folio"],
)
async def get_sale_line(
    env: AuthenticatedEnv,
    line_id: int,
) -> FolioSaleLine:
    """Get a single billable sale line by its id, with invoice state and taxes."""
    helper = env["pms_api_folio.folio_router.helper"].new()
    try:
        line = helper.get_sale_line(line_id)
    except MissingError as err:
        raise HTTPException(
            status_code=404,
            detail="sale line not found",
        ) from err
    return FolioSaleLine.from_folio_sale_line(line)


@pms_api_router.get(
    "/folios/{folio_id}/down-payment-invoices",
    response_model=list[InvoiceSummary],
    tags=["folio"],
)
async def get_folio_down_payment_invoices(
    env: AuthenticatedEnv,
    folio_id: int,
) -> list[InvoiceSummary]:
    """Get the list of down-payment invoices associated with a folio."""
    helper = env["pms_api_folio.folio_router.helper"].new()
    try:
        folio = helper.get(folio_id)
    except MissingError as err:
        raise HTTPException(
            status_code=404,
            detail="folio not found",
        ) from err
    return helper.get_down_payment_invoices(folio)


@pms_api_router.get(
    "/folios/{folio_id}/contacts",
    response_model=list[ContactIdImageEmail],
    tags=["folio"],
)
async def get_folio_contacts(
    env: AuthenticatedEnv,
    folio_id: int,
) -> list[ContactIdImageEmail]:
    """Get all contacts associated with a folio.

    Collects unique contacts from the folio, its reservations, and guests.
    """
    helper = env["pms_api_folio.folio_router.helper"].new()
    try:
        folio = helper.get(folio_id)
    except MissingError as err:
        raise HTTPException(
            status_code=404,
            detail="folio not found",
        ) from err
    return helper.get_contacts(folio)


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

    @property
    def sale_line_adapter(self) -> FilteredModelAdapter[FolioSaleLineModel]:
        # Same scope as the folio sale-line listing: only billable
        # room/service lines, excluding sections, notes and downpayments.
        base_domain = [
            ("display_type", "=", False),
            ("is_downpayment", "=", False),
        ]
        multicompany_domain = self._get_multicompany_rule()
        model_domain = expression.AND([base_domain, multicompany_domain])
        return FilteredModelAdapter[FolioSaleLineModel](self.env, model_domain)

    def get_sale_line(self, line_id) -> FolioSaleLineModel:
        return self.sale_line_adapter.get(line_id)

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

    def get_sale_lines(self, folio) -> list[FolioSaleLine]:
        lines = folio.sale_line_ids.filtered(
            lambda line: not line.display_type and not line.is_downpayment
        )
        return [FolioSaleLine.from_folio_sale_line(line) for line in lines]

    def get_down_payment_invoices(self, folio) -> list[InvoiceSummary]:
        lines = folio.sale_line_ids.filtered("is_downpayment")
        invoices = lines.invoice_lines.move_id
        return [InvoiceSummary.from_account_move(inv) for inv in invoices]

    def get_contacts(self, folio) -> list[ContactIdImageEmail]:
        partners = folio.partner_id
        active_reservations = folio.reservation_ids.filtered(
            lambda r: r.cancelled_reason != "modified"
        )
        partners |= active_reservations.mapped("partner_id").filtered("id")
        partners |= active_reservations.mapped("agency_id").filtered("id")
        partners |= active_reservations.mapped(
            "checkin_partner_ids.partner_id"
        ).filtered("id")
        return [ContactIdImageEmail.from_res_partner(p) for p in partners]

    @api.model
    def extra_features(self):
        return []

    # -- Invoice creation --

    @staticmethod
    def _raise_problem(status, type_, title, detail, **extra):
        raise _InvoiceCreationProblem(
            JSONResponse(
                status_code=status,
                content={
                    "type": type_,
                    "title": title,
                    "status": status,
                    "detail": detail,
                    **extra,
                },
                media_type="application/problem+json",
            )
        )

    def _create_invoice(self, payload: FolioInvoiceCreate):
        try:
            sale_lines = self._resolve_sale_lines(payload)
            downpayment_lines = self._resolve_downpayment_lines(payload, sale_lines)
            pms_property = self._resolve_property(sale_lines)
            self._check_quantities(payload, sale_lines)
            partner = self._resolve_invoice_partner(payload, pms_property)
            if payload.customerId is None:
                self._check_simplified_limit(payload, sale_lines, pms_property)
            invoice = self._build_and_create_invoice(
                payload, sale_lines, downpayment_lines, partner
            )
            self._override_invoice_due_date(invoice, payload)
            if payload.validate_invoice:
                self._post_invoice(invoice)
        except _InvoiceCreationProblem as problem:
            return problem.response
        return InvoiceSummary.from_account_move(invoice)

    def _resolve_sale_lines(self, payload: FolioInvoiceCreate):
        sale_line_ids = [line.saleLineId for line in payload.lines]
        if len(sale_line_ids) != len(set(sale_line_ids)):
            self._raise_problem(
                422,
                "/errors/duplicate-sale-lines",
                "Duplicate sale lines",
                "Each sale line can only appear once in the request.",
            )
        sale_lines = self.env["folio.sale.line"].sudo().browse(sale_line_ids).exists()
        missing = set(sale_line_ids) - set(sale_lines.ids)
        if missing:
            self._raise_problem(
                404,
                "/errors/sale-lines-not-found",
                "Sale lines not found",
                "Some sale lines could not be found.",
                missingSaleLineIds=sorted(missing),
            )
        try:
            PmsBaseModel.pms_api_check_access(self.env.user, sale_lines)
        except (AccessError, AccessDenied):
            self._raise_problem(
                403,
                "/errors/access-denied",
                "Access denied",
                "You are not allowed to access some of the requested sale lines.",
            )
        invalid_kind = sale_lines.filtered(lambda r: r.display_type or r.is_downpayment)
        if invalid_kind:
            self._raise_problem(
                422,
                "/errors/invalid-sale-line",
                "Invalid sale line",
                "Sections, notes and down payments cannot be invoiced through "
                "this endpoint.",
                invalidSaleLineIds=invalid_kind.ids,
            )
        return sale_lines

    def _resolve_downpayment_lines(self, payload: FolioInvoiceCreate, sale_lines):
        if not payload.downpaymentLines:
            return self.env["folio.sale.line"]
        ids = list(set(payload.downpaymentLines))
        if len(ids) != len(payload.downpaymentLines):
            self._raise_problem(
                422,
                "/errors/duplicate-downpayment-lines",
                "Duplicate down-payment invoices",
                "Each down-payment invoice can only appear once in the request.",
            )
        dp_invoices = self.env["account.move"].sudo().browse(ids).exists()
        missing = set(ids) - set(dp_invoices.ids)
        if missing:
            self._raise_problem(
                404,
                "/errors/downpayment-lines-not-found",
                "Down-payment invoices not found",
                "Some down-payment invoices could not be found.",
                missingDownpaymentLineIds=sorted(missing),
            )
        not_downpayment = dp_invoices.filtered(lambda m: not m._is_downpayment())
        if not_downpayment:
            self._raise_problem(
                422,
                "/errors/invalid-downpayment-line",
                "Invalid down-payment invoice",
                "Only down-payment invoices are accepted as downpaymentLines.",
                invalidDownpaymentLineIds=not_downpayment.ids,
            )
        folio_ids = set(sale_lines.folio_id.ids)
        out_of_scope = dp_invoices.filtered(
            lambda m: not set(m.folio_ids.ids) & folio_ids
        )
        if out_of_scope:
            self._raise_problem(
                422,
                "/errors/downpayment-line-out-of-scope",
                "Down-payment invoice out of scope",
                "Down-payment invoices must belong to the same folios as the "
                "invoiced lines.",
                outOfScopeDownpaymentLineIds=out_of_scope.ids,
            )
        return dp_invoices.invoice_line_ids.folio_line_ids.filtered("is_downpayment")

    def _resolve_property(self, sale_lines):
        properties = sale_lines.mapped("pms_property_id")
        if len(properties) > 1:
            self._raise_problem(
                422,
                "/errors/multiple-properties",
                "Sale lines from multiple properties",
                "All sale lines must belong to the same property.",
                propertyIds=properties.ids,
            )
        return properties

    def _check_quantities(self, payload: FolioInvoiceCreate, sale_lines):
        sale_lines_by_id = {sl.id: sl for sl in sale_lines}
        qty_errors = []
        for payload_line in payload.lines:
            sale_line = sale_lines_by_id[payload_line.saleLineId]
            if payload_line.quantityToInvoice > sale_line.qty_to_invoice:
                qty_errors.append(
                    {
                        "saleLineId": sale_line.id,
                        "requested": payload_line.quantityToInvoice,
                        "pending": sale_line.qty_to_invoice,
                    }
                )
        if qty_errors:
            self._raise_problem(
                422,
                "/errors/quantity-exceeds-pending",
                "Quantity to invoice exceeds pending quantity",
                "One or more sale lines were asked to invoice a quantity "
                "greater than their pending quantity.",
                lines=qty_errors,
            )

    def _resolve_invoice_partner(self, payload: FolioInvoiceCreate, pms_property):
        if payload.customerId is None:
            return self.env.ref("pms.various_pms_partner")
        partner = self.env["res.partner"].sudo().browse(payload.customerId).exists()
        if not partner:
            self._raise_problem(
                404,
                "/errors/not-found",
                "Contact not found",
                "Customer not found.",
            )
        invoice_helper = self.env["pms_api_invoice.invoice_router.helper"].new()
        contact_errors = invoice_helper._get_contact_validation_errors(
            partner, pms_property
        )
        if contact_errors:
            self._raise_problem(
                422,
                "/errors/invoicing-validation-failed",
                "Invoicing validation failed",
                "Customer does not meet invoicing requirements.",
                errors=contact_errors,
            )
        return partner

    def _check_simplified_limit(
        self, payload: FolioInvoiceCreate, sale_lines, pms_property
    ):
        limit = pms_property.max_amount_simplified_invoice
        if not limit:
            return
        sale_lines_by_id = {sl.id: sl for sl in sale_lines}
        invoice_total = sum(
            line.quantityToInvoice
            * sale_lines_by_id[line.saleLineId].price_reduce_taxinc
            for line in payload.lines
        )
        if invoice_total > limit:
            self._raise_problem(
                422,
                "/errors/simplified-invoice-limit-exceeded",
                "Simplified invoice limit exceeded",
                "The invoice total exceeds the simplified invoice limit "
                "configured for this property.",
                invoiceTotal=round(invoice_total, 2),
                simplifiedInvoiceLimit=limit,
            )

    def _build_lines_to_invoice(
        self, payload: FolioInvoiceCreate, sale_lines, downpayment_lines
    ) -> dict:
        lines_to_invoice = {
            line.saleLineId: line.quantityToInvoice for line in payload.lines
        }
        for dp in downpayment_lines:
            lines_to_invoice[dp.id] = dp.qty_to_invoice or 1
        lines_with_sections = sale_lines
        for line in sale_lines:
            section = line.section_id
            if section and section.id not in lines_with_sections.ids:
                lines_with_sections |= section
                lines_to_invoice[section.id] = 0
        line_notes = sale_lines.folio_id.sale_line_ids.filtered(
            lambda r: r.display_type == "line_note"
            and r.section_id in lines_with_sections
        )
        for note in line_notes:
            lines_to_invoice.setdefault(note.id, 0)
        return lines_to_invoice

    def _build_and_create_invoice(
        self, payload: FolioInvoiceCreate, sale_lines, downpayment_lines, partner
    ):
        lines_to_invoice = self._build_lines_to_invoice(
            payload, sale_lines, downpayment_lines
        )
        descriptions = {line.saleLineId: line.description for line in payload.lines}
        folios = sale_lines.folio_id.with_context(
            **{FOLIO_INVOICE_LINE_DESCRIPTIONS_CTX: descriptions}
        )
        try:
            invoices = folios._create_invoices(
                lines_to_invoice=lines_to_invoice,
                partner_invoice_id=partner.id,
                date=payload.invoiceDate,
                final=True,
            )
        except UserError as e:
            self._raise_problem(
                422,
                "/errors/invoice-creation-failed",
                "Invoice creation failed",
                str(e),
            )
        if not invoices:
            self._raise_problem(
                422,
                "/errors/invoice-creation-failed",
                "Invoice creation failed",
                "No invoice could be created from the provided sale lines.",
            )
        if len(invoices) > 1:
            self._raise_problem(
                422,
                "/errors/multiple-invoices-created",
                "Multiple invoices created",
                "The provided sale lines could not be grouped into a single "
                "invoice (different currency or company).",
                invoiceIds=invoices.ids,
            )
        return invoices

    @staticmethod
    def _override_invoice_due_date(invoice, payload: FolioInvoiceCreate):
        if payload.dueDate and payload.dueDate != invoice.invoice_date_due:
            invoice.write({"invoice_date_due": payload.dueDate})

    def _post_invoice(self, invoice):
        try:
            invoice.action_post()
        except UserError as e:
            self._raise_problem(
                422,
                "/errors/invoice-posting-failed",
                "Invoice posting failed",
                str(e),
            )

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
