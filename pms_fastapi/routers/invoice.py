import re
from typing import Annotated

from fastapi import Depends, Query
from fastapi.responses import JSONResponse, Response

from odoo import SUPERUSER_ID, api, fields, models
from odoo.exceptions import MissingError, UserError
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
from odoo.addons.pms_fastapi.models.folio_sale_line import (
    FOLIO_INVOICE_LINE_DESCRIPTIONS_CTX,
)
from odoo.addons.pms_fastapi.schemas.invoice import (
    INVOICE_ORDER_MAPPING,
    InvoiceDetail,
    InvoiceInput,
    InvoiceOrderField,
    InvoiceSearch,
    InvoiceSummary,
    ReconcilablePayment,
    ReconciliationCreate,
    ReportFormatEnum,
    ShareUrl,
)
from odoo.addons.pms_fastapi.utils import FilteredModelAdapter


class _InvoiceEditProblem(Exception):
    """Control-flow exception carrying an RFC 9457 JSONResponse."""

    def __init__(self, response):
        super().__init__()
        self.response = response


INVOICE_REPORT_MAX_RECORDS = 5000
INVOICE_PDF_REPORT_MAX_RECORDS = 100

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


@pms_api_router.get(
    "/invoices/validate-contact",
    tags=["invoice"],
    status_code=204,
    responses={
        204: {"description": "Contact meets invoicing requirements."},
        422: {"description": "Contact does not meet invoicing requirements."},
    },
    response_class=Response,
)
async def validate_invoice_contact(
    env: AuthenticatedEnv,
    pmsPropertyId: Annotated[
        int,
        Query(description="ID of the property whose invoicing rules are validated."),
    ],
    contactId: Annotated[
        int,
        Query(description="ID of the contact to validate as invoicing customer."),
    ],
) -> Response:
    """Validate if a contact meets the invoicing requirements of a property."""
    return (
        env["pms_api_invoice.invoice_router.helper"]
        .new()
        ._validate_contact(pmsPropertyId, contactId)
    )


@pms_api_router.put(
    "/invoices/{invoice_id}",
    response_model=InvoiceDetail,
    tags=["invoice"],
)
async def edit_invoice(
    env: AuthenticatedEnv,
    invoice_id: int,
    payload: InvoiceInput,
    confirmRefund: Annotated[
        bool,
        Query(
            description=(
                "Required when the invoice is validated. Without this flag, "
                "editing a validated invoice returns 409."
            ),
        ),
    ] = False,
):
    """Edit an existing invoice as a full replacement.

    Drafts are rewritten in-place (same id). Validated invoices, when
    confirmRefund=true, generate a full refund that cancels the original
    and a new corrected invoice in draft (different id) whose `replaces`
    points to the original.
    """
    return (
        env["pms_api_invoice.invoice_router.helper"]
        .new()
        ._edit_invoice(invoice_id, payload, confirmRefund)
    )


@pms_api_router.post(
    "/invoices/{id}/validate",
    response_model=InvoiceSummary,
    tags=["invoice"],
)
async def validate_invoice(
    env: AuthenticatedEnv,
    id: int,
) -> InvoiceSummary:
    """Validate a draft invoice."""
    return env["pms_api_invoice.invoice_router.helper"].new()._validate_invoice(id)


_PAYMENT_ID_RE = re.compile(r"^payment_(\d+)$")


@pms_api_router.post(
    "/invoices/{invoice_id}/reconciliations",
    response_model=InvoiceDetail,
    tags=["invoice"],
)
async def create_reconciliation(
    env: AuthenticatedEnv,
    invoice_id: int,
    payload: ReconciliationCreate,
):
    """Reconcile an existing payment with the invoice.

    The backend decides how much of the payment is applied, up to the
    invoice's residual amount.
    """
    return (
        env["pms_api_invoice.invoice_router.helper"]
        .new()
        ._create_reconciliation(invoice_id, payload)
    )


@pms_api_router.delete(
    "/invoices/{invoice_id}/reconciliations/{reconciliation_id}",
    response_model=InvoiceDetail,
    tags=["invoice"],
)
async def delete_reconciliation(
    env: AuthenticatedEnv,
    invoice_id: int,
    reconciliation_id: str,
):
    """Undo a previously applied reconciliation between a payment and the invoice."""
    return (
        env["pms_api_invoice.invoice_router.helper"]
        .new()
        ._delete_reconciliation(invoice_id, reconciliation_id)
    )


@pms_api_router.get(
    "/invoices/{invoice_id}/reconcilable-payments",
    response_model=list[ReconcilablePayment],
    tags=["invoice"],
)
async def list_reconcilable_payments(
    env: AuthenticatedEnv,
    invoice_id: int,
):
    """List payments that can be reconciled with the given invoice.

    Returns payments belonging to the folio associated with the invoice,
    or payments of the invoice's customer when the invoice has no folio.
    Only payments with available (non-fully-reconciled) amount are returned.
    """
    return (
        env["pms_api_invoice.invoice_router.helper"]
        .new()
        ._get_reconcilable_payments(invoice_id)
    )


@pms_api_router.get(
    "/invoices/{id}/share",
    response_model=ShareUrl,
    tags=["invoice"],
)
async def get_invoice_share_url(
    env: AuthenticatedEnv,
    id: int,
) -> ShareUrl:
    """Get a public share URL with token for the given invoice."""
    return env["pms_api_invoice.invoice_router.helper"].new()._get_invoice_share_url(id)


@pms_api_router.get(
    "/invoices/{id}/report",
    tags=["invoice"],
    responses={
        200: {"content": {"application/pdf": {}}},
    },
    response_class=Response,
)
async def get_invoice_report(
    env: AuthenticatedEnv,
    id: int,
) -> Response:
    """Download the PDF report for a specific invoice."""
    return env["pms_api_invoice.invoice_router.helper"].new()._get_invoice_pdf(id)


@pms_api_router.post(
    "/invoices/report",
    tags=["invoice"],
    responses={
        200: {"content": {"application/pdf": {}}},
    },
    response_class=Response,
)
async def get_invoices_report(
    env: AuthenticatedEnv,
    filters: Annotated[InvoiceSearch, Depends()],
    ids: Annotated[
        list[int] | None,
        Query(
            description="Invoice IDs to include in the report. "
            "Mutually exclusive with filter parameters.",
        ),
    ] = None,
) -> Response:
    """Download a single PDF with one or more invoices.

    Pass either an explicit list of `ids` or filter parameters.
    Returns the same PDF format as downloading an individual invoice,
    concatenating all selected invoices.
    """
    return (
        env["pms_api_invoice.invoice_router.helper"]
        .new()
        ._get_invoices_pdf(filters, ids)
    )


@pms_api_router.post(
    "/invoices/accounting-report",
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

    # -- Invoice edit (PUT /invoices/{id}) --

    @staticmethod
    def _raise_edit_problem(status, type_, title, detail, **extra):
        raise _InvoiceEditProblem(
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

    def _edit_invoice(self, invoice_id, payload, confirm_refund):
        try:
            invoice = self.get(invoice_id)
        except MissingError:
            return JSONResponse(
                status_code=404,
                content={
                    "type": "/errors/not-found",
                    "title": "Not found",
                    "status": 404,
                    "detail": "Invoice not found.",
                },
                media_type="application/problem+json",
            )
        try:
            if invoice.state == "cancel":
                self._raise_edit_problem(
                    409,
                    "/errors/invoice-not-editable",
                    "Invoice not editable",
                    "Cancelled invoices cannot be edited.",
                )
            if invoice.state == "posted" and not confirm_refund:
                self._raise_edit_problem(
                    409,
                    "/errors/invoice-refund-confirmation-required",
                    "Refund confirmation required",
                    (
                        "This invoice is validated. Editing it will generate "
                        "a refund and a new corrected invoice. Confirm by "
                        "passing confirmRefund=true."
                    ),
                )
            sale_lines = self._edit_resolve_sale_lines(payload)
            downpayment_lines = self._edit_resolve_downpayment_lines(
                payload, sale_lines
            )
            pms_property = self._edit_resolve_property(sale_lines)
            partner = self._edit_resolve_partner(payload, pms_property)
            self._edit_check_quantities(payload, sale_lines, current_invoice=invoice)
            self._edit_check_composition(
                sale_lines, downpayment_lines, current_invoice=invoice
            )
            if invoice.state == "draft":
                result = self._edit_draft_invoice(
                    invoice, payload, sale_lines, downpayment_lines, partner
                )
            else:
                result = self._edit_posted_invoice(
                    invoice, payload, sale_lines, downpayment_lines, partner
                )
        except _InvoiceEditProblem as problem:
            return problem.response
        return InvoiceDetail.from_account_move(result)

    def _edit_resolve_sale_lines(self, payload):
        line_ids = [fl.id for fl in payload.folioLines]
        if len(line_ids) != len(set(line_ids)):
            self._raise_edit_problem(
                422,
                "/errors/duplicate-sale-lines",
                "Duplicate sale lines",
                "Each folio line can only appear once in the request.",
            )
        sale_lines = self.env["folio.sale.line"].sudo().browse(line_ids).exists()
        missing = set(line_ids) - set(sale_lines.ids)
        if missing:
            self._raise_edit_problem(
                404,
                "/errors/sale-lines-not-found",
                "Sale lines not found",
                "Some folio lines could not be found.",
                missingFolioLineIds=sorted(missing),
            )
        invalid_kind = sale_lines.filtered(lambda r: r.display_type or r.is_downpayment)
        if invalid_kind:
            self._raise_edit_problem(
                422,
                "/errors/invalid-folio-line",
                "Invalid folio line",
                "Sections, notes and down payments cannot be sent as folioLines.",
                invalidFolioLineIds=invalid_kind.ids,
            )
        return sale_lines

    def _edit_resolve_downpayment_lines(self, payload, sale_lines):
        if not payload.downpaymentLines:
            return self.env["folio.sale.line"]
        ids = list(set(payload.downpaymentLines))
        if len(ids) != len(payload.downpaymentLines):
            self._raise_edit_problem(
                422,
                "/errors/duplicate-downpayment-lines",
                "Duplicate down-payment lines",
                "Each down-payment line can only appear once in the request.",
            )
        dp_lines = self.env["folio.sale.line"].sudo().browse(ids).exists()
        missing = set(ids) - set(dp_lines.ids)
        if missing:
            self._raise_edit_problem(
                404,
                "/errors/downpayment-lines-not-found",
                "Down-payment lines not found",
                "Some down-payment lines could not be found.",
                missingDownpaymentLineIds=sorted(missing),
            )
        not_downpayment = dp_lines.filtered(lambda r: not r.is_downpayment)
        if not_downpayment:
            self._raise_edit_problem(
                422,
                "/errors/invalid-downpayment-line",
                "Invalid down-payment line",
                "Only down-payment lines are accepted as downpaymentLines.",
                invalidDownpaymentLineIds=not_downpayment.ids,
            )
        folio_ids = set(sale_lines.folio_id.ids)
        out_of_scope = dp_lines.filtered(lambda r: r.folio_id.id not in folio_ids)
        if out_of_scope:
            self._raise_edit_problem(
                422,
                "/errors/downpayment-line-out-of-scope",
                "Down-payment line out of scope",
                "Down-payment lines must belong to the same folios as folioLines.",
                outOfScopeDownpaymentLineIds=out_of_scope.ids,
            )
        return dp_lines

    def _edit_resolve_property(self, sale_lines):
        properties = sale_lines.mapped("pms_property_id")
        if len(properties) > 1:
            self._raise_edit_problem(
                422,
                "/errors/multiple-properties",
                "Folio lines from multiple properties",
                "All folio lines must belong to the same property.",
                propertyIds=properties.ids,
            )
        return properties

    def _edit_resolve_partner(self, payload, pms_property):
        partner = self.env["res.partner"].sudo().browse(payload.partner).exists()
        if not partner:
            self._raise_edit_problem(
                404,
                "/errors/not-found",
                "Contact not found",
                "Customer not found.",
            )
        various = self.env.ref("pms.various_pms_partner", raise_if_not_found=False)
        if various and partner.id == various.id:
            return partner
        errors = self._get_contact_validation_errors(partner, pms_property)
        if errors:
            self._raise_edit_problem(
                422,
                "/errors/invoicing-validation-failed",
                "Invoicing validation failed",
                "Customer does not meet invoicing requirements.",
                errors=errors,
            )
        return partner

    def _edit_check_quantities(self, payload, sale_lines, current_invoice):
        sale_lines_by_id = {sl.id: sl for sl in sale_lines}
        qty_errors = []
        # When editing a draft, requested qty can include the qty already
        # invoiced by this very draft (it'll be released on rewrite).
        current_invoice_lines = (
            current_invoice.invoice_line_ids if current_invoice else None
        )
        for payload_line in payload.folioLines:
            sale_line = sale_lines_by_id[payload_line.id]
            available = sale_line.qty_to_invoice
            if current_invoice_lines:
                available += sum(
                    line.quantity
                    for line in current_invoice_lines
                    if sale_line in line.folio_line_ids
                )
            if payload_line.quantity > available:
                qty_errors.append(
                    {
                        "folioLineId": sale_line.id,
                        "requested": payload_line.quantity,
                        "pending": available,
                    }
                )
        if qty_errors:
            self._raise_edit_problem(
                422,
                "/errors/quantity-exceeds-pending",
                "Quantity to invoice exceeds pending quantity",
                "One or more folio lines were asked to invoice more "
                "than their pending quantity.",
                lines=qty_errors,
            )

    def _edit_check_composition(self, sale_lines, downpayment_lines, current_invoice):
        composition_errors = []
        current_invoice_id = current_invoice.id if current_invoice else None
        for sl in sale_lines | downpayment_lines:
            other_moves = sl.invoice_lines.move_id.filtered(
                lambda m: m.state != "cancel" and m.id != current_invoice_id
            )
            if other_moves:
                composition_errors.append(
                    {
                        "type": "/errors/folio-line-already-invoiced",
                        "title": "Folio line already invoiced",
                        "detail": (
                            f"Folio line {sl.id} is already included in "
                            f"invoice {other_moves[0].id}."
                        ),
                    }
                )
        if composition_errors:
            self._raise_edit_problem(
                422,
                "/errors/invoice-composition-invalid",
                "Invalid invoice composition",
                "Some folio lines cannot be invoiced.",
                errors=composition_errors,
            )

    @staticmethod
    def _edit_build_lines_to_invoice(payload, sale_lines, downpayment_lines):
        lines_to_invoice = {fl.id: fl.quantity for fl in payload.folioLines}
        for dp in downpayment_lines:
            lines_to_invoice[dp.id] = dp.qty_to_invoice or 1
        # Include sections and notes referenced by the regular sale lines
        # so the resulting invoice keeps its structure.
        section_ids = sale_lines.mapped("section_id")
        for section in section_ids:
            lines_to_invoice.setdefault(section.id, 0)
        sections_in_scope = section_ids | sale_lines.filtered(
            lambda r: r.display_type == "line_section"
        )
        notes = sale_lines.folio_id.sale_line_ids.filtered(
            lambda r: r.display_type == "line_note"
            and r.section_id in sections_in_scope
        )
        for note in notes:
            lines_to_invoice.setdefault(note.id, 0)
        return lines_to_invoice

    def _edit_compute_invoice_vals(
        self, payload, sale_lines, downpayment_lines, partner
    ):
        lines_to_invoice = self._edit_build_lines_to_invoice(
            payload, sale_lines, downpayment_lines
        )
        descriptions = {fl.id: fl.description for fl in payload.folioLines}
        folios = sale_lines.folio_id.with_context(
            **{FOLIO_INVOICE_LINE_DESCRIPTIONS_CTX: descriptions}
        )
        try:
            vals_list = folios.get_invoice_vals_list(
                final=True,
                lines_to_invoice=lines_to_invoice,
                partner_invoice_id=partner.id,
            )
        except UserError as e:
            self._raise_edit_problem(
                422,
                "/errors/invoice-creation-failed",
                "Invoice creation failed",
                str(e),
            )
        if not vals_list:
            self._raise_edit_problem(
                422,
                "/errors/invoice-creation-failed",
                "Invoice creation failed",
                "No invoice could be built from the provided composition.",
            )
        if len(vals_list) > 1:
            self._raise_edit_problem(
                422,
                "/errors/multiple-invoices-created",
                "Multiple invoices created",
                "The provided composition could not be grouped into a single "
                "invoice (different currency or company).",
            )
        return vals_list[0]

    def _edit_draft_invoice(
        self, invoice, payload, sale_lines, downpayment_lines, partner
    ):
        # Release the quantity held by this draft before recomputing the
        # composition, so folio lines fully invoiced by this very draft can be
        # re-invoiced (otherwise their qty_to_invoice is 0 at compute time and
        # the rewritten invoice ends up empty).
        invoice.invoice_line_ids.unlink()
        vals = self._edit_compute_invoice_vals(
            payload, sale_lines, downpayment_lines, partner
        )
        write_vals = {
            "partner_id": partner.id,
            "narration": payload.narration,
            "invoice_line_ids": vals["invoice_line_ids"],
        }
        if payload.invoiceDate:
            write_vals["invoice_date"] = payload.invoiceDate
        if payload.invoiceDateDue:
            write_vals["invoice_date_due"] = payload.invoiceDateDue
        invoice.write(write_vals)
        return invoice

    def _edit_posted_invoice(
        self, invoice, payload, sale_lines, downpayment_lines, partner
    ):
        receivable_lines = invoice.line_ids.filtered(
            lambda line: line.account_id.account_type
            in ("asset_receivable", "liability_payable")
        )
        receivable_lines.remove_move_reconcile()
        reversal_date = fields.Date.context_today(invoice)
        invoice._reverse_moves(
            default_values_list=[
                {
                    "ref": f"Reversal of: {invoice.name}",
                    "date": reversal_date,
                    "invoice_date": reversal_date,
                    "invoice_date_due": reversal_date,
                    "journal_id": invoice.journal_id.id,
                }
            ],
            cancel=True,
        )
        vals = self._edit_compute_invoice_vals(
            payload, sale_lines, downpayment_lines, partner
        )
        if payload.invoiceDate:
            vals["invoice_date"] = payload.invoiceDate
            vals["date"] = payload.invoiceDate
        if payload.invoiceDateDue:
            vals["invoice_date_due"] = payload.invoiceDateDue
        vals["narration"] = payload.narration
        vals["replaces_invoice_id"] = invoice.id
        try:
            new_invoice = self.env["account.move"].sudo().create(vals)
        except UserError as e:
            self._raise_edit_problem(
                422,
                "/errors/invoice-creation-failed",
                "Invoice creation failed",
                str(e),
            )
        return new_invoice

    def _validate_contact(self, pms_property_id: int, contact_id: int):
        partner = self.env["res.partner"].sudo().browse(contact_id)
        if not partner.exists():
            return JSONResponse(
                status_code=404,
                content={
                    "type": "/errors/not-found",
                    "title": "Not found",
                    "status": 404,
                    "detail": "Contact not found.",
                },
                media_type="application/problem+json",
            )
        pms_property = self.env["pms.property"].sudo().browse(pms_property_id)
        if not pms_property.exists():
            return JSONResponse(
                status_code=404,
                content={
                    "type": "/errors/not-found",
                    "title": "Not found",
                    "status": 404,
                    "detail": "Property not found.",
                },
                media_type="application/problem+json",
            )
        errors = self._get_contact_validation_errors(partner, pms_property)
        if errors:
            return JSONResponse(
                status_code=422,
                content={
                    "type": "/errors/invoicing-validation-failed",
                    "title": "Invoicing validation failed",
                    "status": 422,
                    "detail": "Contact does not meet invoicing requirements.",
                    "errors": errors,
                },
                media_type="application/problem+json",
            )
        return Response(status_code=204)

    def _get_contact_validation_errors(self, partner, pms_property) -> list[dict]:
        """Return RFC 9457 ProblemDetailItem dicts for each validation failure.

        Override in localization modules to add country-specific checks.
        """
        errors = []
        errors.extend(self._check_fiscal_id(partner, pms_property))
        return errors

    def _check_fiscal_id(self, partner, pms_property) -> list[dict]:
        """Check fiscal identification. Override in localizations.

        Base implementation requires a VAT number.
        """
        if not partner.vat:
            return [
                {
                    "type": "/errors/missing-fiscal-id",
                    "title": "Missing fiscal identification number",
                    "detail": (
                        "El contacto no tiene número de identificación"
                        " fiscal configurado."
                    ),
                }
            ]
        return []

    # -- Invoice report helpers --

    @staticmethod
    def _has_report_filters(filters):
        return any(v is not None for v in filters.__dict__.values())

    def _resolve_report_invoices(
        self, filters, ids, max_records=INVOICE_REPORT_MAX_RECORDS
    ):
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
            ), None
        if ids:
            domain = [("id", "in", ids)]
            context = None
        else:
            domain = filters.to_odoo_domain(self.env)
            context = filters.to_odoo_context(self.env)
        count = (
            self.model_adapter.count(domain)
            if context is None
            else self.model_adapter.count(domain, context=context)
        )
        if count > max_records:
            return JSONResponse(
                status_code=400,
                content={
                    "type": "/errors/record-limit-exceeded",
                    "title": "Record limit exceeded",
                    "status": 400,
                    "detail": (
                        f"The export requested {count} records, "
                        f"but the maximum allowed is {max_records}."
                    ),
                    "requestedCount": count,
                    "maxAllowed": max_records,
                },
                media_type="application/problem+json",
            ), None
        invoices = (
            self.model_adapter.search(domain)
            if context is None
            else self.model_adapter.search(domain, context=context)
        )
        return None, invoices

    def _generate_report(self, report_format, filters, ids):
        error, invoices = self._resolve_report_invoices(filters, ids)
        if error is not None:
            return error
        if report_format == ReportFormatEnum.xlsx:
            return self._render_invoice_xlsx(invoices)
        return self._render_invoice_pdf(invoices)

    def _validate_invoice(self, record_id):
        try:
            invoice = self.get(record_id)
        except MissingError:
            return JSONResponse(
                status_code=404,
                content={
                    "type": "/errors/not-found",
                    "title": "Not found",
                    "status": 404,
                    "detail": "Invoice not found.",
                },
                media_type="application/problem+json",
            )
        if invoice.state != "draft":
            return JSONResponse(
                status_code=400,
                content={
                    "type": "/errors/invoice-not-draft",
                    "title": "Invoice not in draft state",
                    "status": 400,
                    "detail": (
                        f"Invoice {invoice.name} is in state "
                        f'"{invoice.state}" and cannot be validated.'
                    ),
                },
                media_type="application/problem+json",
            )
        try:
            invoice.action_post()
        except UserError as e:
            return JSONResponse(
                status_code=400,
                content={
                    "type": "/errors/validation-error",
                    "title": "Validation error",
                    "status": 400,
                    "detail": str(e),
                },
                media_type="application/problem+json",
            )
        return InvoiceSummary.from_account_move(invoice)

    @staticmethod
    def _parse_payment_composite_id(composite_id):
        """Return the int payment id from 'payment_{id}', or raise 422."""
        match = _PAYMENT_ID_RE.match(composite_id or "")
        if not match:
            PmsApiInvoiceRouterHelper._raise_edit_problem(
                422,
                "/errors/invalid-reconciliation-id",
                "Invalid reconciliation id",
                (
                    f"Reconciliation id '{composite_id}' is malformed. "
                    "Expected 'payment_{id}'."
                ),
            )
        return int(match.group(1))

    def _get_reconciliation_partials(self, invoice, payment):
        invoice_recv = invoice.line_ids.filtered(
            lambda line: line.account_id.account_type
            in ("asset_receivable", "liability_payable")
        )
        payment_recv = payment.move_id.line_ids.filtered(
            lambda line: line.account_id.account_type
            in ("asset_receivable", "liability_payable")
        )
        partials = invoice_recv.matched_debit_ids.filtered(
            lambda p: p.debit_move_id in payment_recv
        ) | invoice_recv.matched_credit_ids.filtered(
            lambda p: p.credit_move_id in payment_recv
        )
        return invoice_recv, payment_recv, partials

    def _create_reconciliation(self, invoice_id, payload):
        try:
            invoice = self.get(invoice_id)
        except MissingError:
            return JSONResponse(
                status_code=404,
                content={
                    "type": "/errors/invoice-not-found",
                    "title": "Invoice not found",
                    "status": 404,
                    "detail": "Invoice not found.",
                },
                media_type="application/problem+json",
            )
        try:
            payment_id = self._parse_payment_composite_id(payload.paymentId)
            if invoice.state != "posted" or invoice.payment_state in (
                "paid",
                "reversed",
            ):
                self._raise_edit_problem(
                    409,
                    "/errors/invoice-not-editable",
                    "Invoice not editable",
                    "The invoice state does not allow new reconciliations.",
                )
            payment = (
                self.env["account.payment"]
                .sudo()
                .search(
                    [
                        ("id", "=", payment_id),
                        ("company_id", "=", invoice.company_id.id),
                    ],
                    limit=1,
                )
            )
            if not payment:
                self._raise_edit_problem(
                    404,
                    "/errors/payment-not-found",
                    "Payment not found",
                    f"Payment {payment_id} not found.",
                )
            if invoice.folio_ids:
                if not (payment.folio_ids & invoice.folio_ids):
                    self._raise_edit_problem(
                        409,
                        "/errors/payment-not-applicable",
                        "Payment not applicable",
                        ("Payment does not belong to any folio of this " "invoice."),
                    )
            elif (
                payment.partner_id.commercial_partner_id
                != invoice.commercial_partner_id
            ):
                self._raise_edit_problem(
                    409,
                    "/errors/payment-not-applicable",
                    "Payment not applicable",
                    "Payment does not belong to the invoice's customer.",
                )
            invoice_recv, payment_recv, partials = self._get_reconciliation_partials(
                invoice, payment
            )
            if partials:
                self._raise_edit_problem(
                    409,
                    "/errors/payment-already-reconciled",
                    "Payment already reconciled",
                    (
                        f"Payment {payment.id} is already reconciled with "
                        f"invoice {invoice.id}."
                    ),
                )
            if not invoice_recv or not payment_recv:
                self._raise_edit_problem(
                    409,
                    "/errors/payment-not-applicable",
                    "Payment not applicable",
                    "Payment is not in a reconcilable state.",
                )
            try:
                (invoice_recv + payment_recv).filtered(
                    lambda line: not line.reconciled
                ).reconcile()
            except UserError as e:
                self._raise_edit_problem(
                    409,
                    "/errors/payment-not-applicable",
                    "Payment not applicable",
                    str(e),
                )
        except _InvoiceEditProblem as problem:
            return problem.response
        # Reconciling updated payment_state/amount_residual on the stored
        # record; drop the stale cache so the response reflects the new state.
        invoice.invalidate_recordset()
        return InvoiceDetail.from_account_move(invoice)

    def _delete_reconciliation(self, invoice_id, reconciliation_id):
        try:
            invoice = self.get(invoice_id)
        except MissingError:
            return JSONResponse(
                status_code=404,
                content={
                    "type": "/errors/invoice-not-found",
                    "title": "Invoice not found",
                    "status": 404,
                    "detail": "Invoice not found.",
                },
                media_type="application/problem+json",
            )
        try:
            payment_id = self._parse_payment_composite_id(reconciliation_id)
            if invoice.state != "posted":
                self._raise_edit_problem(
                    409,
                    "/errors/invoice-not-editable",
                    "Invoice not editable",
                    "The invoice state does not allow undoing reconciliations.",
                )
            payment = (
                self.env["account.payment"]
                .sudo()
                .search(
                    [
                        ("id", "=", payment_id),
                        ("company_id", "=", invoice.company_id.id),
                    ],
                    limit=1,
                )
            )
            if not payment:
                self._raise_edit_problem(
                    404,
                    "/errors/reconciliation-not-found",
                    "Reconciliation not found",
                    (
                        f"No reconciliation exists between invoice {invoice.id} "
                        f"and payment {payment_id}."
                    ),
                )
            _invoice_recv, _payment_recv, partials = self._get_reconciliation_partials(
                invoice, payment
            )
            if not partials:
                self._raise_edit_problem(
                    404,
                    "/errors/reconciliation-not-found",
                    "Reconciliation not found",
                    (
                        f"No reconciliation exists between invoice {invoice.id} "
                        f"and payment {payment.id}."
                    ),
                )
            partials.unlink()
        except _InvoiceEditProblem as problem:
            return problem.response
        # Undoing the reconciliation updated payment_state/amount_residual on
        # the stored record; drop the stale cache before serializing.
        invoice.invalidate_recordset()
        return InvoiceDetail.from_account_move(invoice)

    def _get_reconcilable_payments(self, invoice_id):
        try:
            invoice = self.get(invoice_id)
        except MissingError:
            return JSONResponse(
                status_code=404,
                content={
                    "type": "/errors/not-found",
                    "title": "Not found",
                    "status": 404,
                    "detail": "Invoice not found.",
                },
                media_type="application/problem+json",
            )
        if invoice.state != "posted":
            return []
        pay_term_lines = invoice.line_ids.filtered(
            lambda line: line.account_id.account_type
            in ("asset_receivable", "liability_payable")
        )
        if not pay_term_lines:
            return []
        domain = [
            ("account_id", "in", pay_term_lines.account_id.ids),
            ("parent_state", "=", "posted"),
            ("reconciled", "=", False),
            "|",
            ("amount_residual", "!=", 0.0),
            ("amount_residual_currency", "!=", 0.0),
            ("payment_id", "!=", False),
        ]
        if invoice.is_inbound():
            domain.append(("balance", "<", 0.0))
        else:
            domain.append(("balance", ">", 0.0))
        folio_ids = invoice.folio_ids.ids
        if folio_ids:
            domain.append(("folio_ids", "in", folio_ids))
        else:
            domain.append(("partner_id", "=", invoice.commercial_partner_id.id))
        lines = self.env["account.move.line"].sudo().search(domain)
        items = []
        for line in lines:
            payment = line.payment_id
            pay_currency = payment.currency_id or payment.company_id.currency_id
            available_currency = abs(line.amount_residual_currency)
            available_company = abs(line.amount_residual)
            if pay_currency.is_zero(available_currency):
                continue
            items.append(
                ReconcilablePayment.from_account_payment(
                    payment, available_currency, available_company
                )
            )
        return items

    def _get_invoice_share_url(self, record_id):
        try:
            invoice = self.get(record_id)
        except MissingError:
            return JSONResponse(
                status_code=404,
                content={
                    "type": "/errors/not-found",
                    "title": "Not found",
                    "status": 404,
                    "detail": "Invoice not found.",
                },
                media_type="application/problem+json",
            )
        if invoice.state == "draft":
            relative_url = invoice.get_proforma_portal_url()
        else:
            relative_url = invoice.get_portal_url()
        base_url = self.env["ir.config_parameter"].sudo().get_param("web.base.url")
        return ShareUrl(url=base_url + relative_url)

    def _get_invoice_pdf(self, record_id):
        try:
            invoice = self.get(record_id)
        except MissingError:
            return JSONResponse(
                status_code=404,
                content={
                    "type": "/errors/not-found",
                    "title": "Not found",
                    "status": 404,
                    "detail": "Invoice not found.",
                },
                media_type="application/problem+json",
            )
        content, _report_type = (
            self.env["ir.actions.report"]
            .sudo()
            ._render_qweb_pdf("account.account_invoices", [invoice.id])
        )
        filename = invoice._get_report_attachment_filename()
        return Response(
            content=content,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
            },
        )

    def _get_invoices_pdf(self, filters, ids):
        error, invoices = self._resolve_report_invoices(
            filters, ids, max_records=INVOICE_PDF_REPORT_MAX_RECORDS
        )
        if error is not None:
            return error
        content, _report_type = (
            self.env["ir.actions.report"]
            .sudo()
            ._render_qweb_pdf("account.account_invoices", invoices.ids)
        )
        return Response(
            content=content,
            media_type="application/pdf",
            headers={
                "Content-Disposition": 'attachment; filename="invoices_report.pdf"',
            },
        )

    def _get_invoice_xlsx_report_name(self):
        return "roomdoo_invoices_exporter.invoice_payment_report"

    def _render_invoice_xlsx(self, invoices):
        report_name = self._get_invoice_xlsx_report_name()
        # `report_xlsx._render_xlsx` forces `.sudo(False)` on the report
        # model, so `.sudo()` here would not bypass ACL inside the export.
        content, _report_type = (
            self.env["ir.actions.report"]
            .with_user(SUPERUSER_ID)
            ._render(report_name, invoices.ids)
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
