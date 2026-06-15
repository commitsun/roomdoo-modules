from typing import Annotated

from fastapi import Depends
from fastapi.responses import JSONResponse

from odoo import models
from odoo.exceptions import AccessDenied, AccessError

from odoo.addons.account.models.account_payment import AccountPayment
from odoo.addons.extendable_fastapi.schemas import PagedCollection
from odoo.addons.fastapi.dependencies import paging
from odoo.addons.fastapi.schemas import Paging
from odoo.addons.pms_fastapi.dependencies import (
    AuthenticatedEnv,
    create_order_dependency,
)
from odoo.addons.pms_fastapi.models.fastapi_endpoint import pms_api_router
from odoo.addons.pms_fastapi.schemas.base import PmsBaseModel
from odoo.addons.pms_fastapi.schemas.payment import (
    PAYMENT_ORDER_MAPPING,
    InternalTransferInput,
    PaymentCreateType,
    PaymentInput,
    PaymentOrderField,
    PaymentSearch,
    PaymentSummary,
)
from odoo.addons.pms_fastapi.utils import FilteredModelAdapter

PaymentOrderDependency = create_order_dependency(
    PaymentOrderField, PAYMENT_ORDER_MAPPING, ["-date"]
)

# (payment_type, partner_type) per creatable payment type.
_CREATE_TYPE_FIELDS = {
    PaymentCreateType.customerPayment: ("inbound", "customer"),
    PaymentCreateType.supplierPayment: ("outbound", "supplier"),
}


class _PaymentProblem(Exception):
    """Control-flow exception carrying an RFC 9457 JSONResponse."""

    def __init__(self, response):
        super().__init__()
        self.response = response


@pms_api_router.get(
    "/payments",
    response_model=PagedCollection[PaymentSummary],
    tags=["payment"],
)
async def list_payments(
    env: AuthenticatedEnv,
    filters: Annotated[PaymentSearch, Depends()],
    paging: Annotated[Paging, Depends(paging)],
    orderBy: Annotated[str, Depends(PaymentOrderDependency)],
) -> PagedCollection[PaymentSummary]:
    """List payments (customer/supplier payments and refunds, internal
    transfers) with pagination and filtering."""
    count, payments = (
        env["pms_api_payment.payment_router.helper"]
        .new()
        ._search(paging, filters, orderBy)
    )
    return PagedCollection[PaymentSummary](
        count=count,
        items=[PaymentSummary.from_account_payment(payment) for payment in payments],
    )


@pms_api_router.get(
    "/payments/{payment_id}",
    response_model=PaymentSummary,
    tags=["payment"],
)
async def get_payment(
    env: AuthenticatedEnv,
    payment_id: int,
) -> PaymentSummary:
    """Get a single payment by id (same model as the listing)."""
    return env["pms_api_payment.payment_router.helper"].new().get(payment_id)


@pms_api_router.post(
    "/payments",
    response_model=PaymentSummary,
    status_code=201,
    tags=["payment"],
)
async def create_payment(
    env: AuthenticatedEnv,
    payload: PaymentInput,
) -> PaymentSummary:
    """Register a manual customer payment or supplier payment.

    The journal is derived from the payment method (paymentMethodId). Customer
    payments may carry a folio or invoice context (mutually exclusive)."""
    return env["pms_api_payment.payment_router.helper"].new().create_payment(payload)


@pms_api_router.post(
    "/internal-transfers",
    response_model=PaymentSummary,
    status_code=201,
    tags=["payment"],
)
async def create_internal_transfer(
    env: AuthenticatedEnv,
    payload: InternalTransferInput,
) -> PaymentSummary:
    """Register an internal transfer between two journals."""
    return (
        env["pms_api_payment.payment_router.helper"]
        .new()
        .create_internal_transfer(payload)
    )


class PmsApiPaymentRouterHelper(models.AbstractModel):
    _name = "pms_api_payment.payment_router.helper"
    _description = "PMS API Payment Router Helper"

    def _get_domain_adapter(self):
        # Posted payments only. Internal transfers are stored as two paired
        # account.payment records (inbound + outbound) and BOTH legs are
        # returned, replicating the legacy API behaviour (no dedup).
        return [("state", "=", "posted")]

    @property
    def model_adapter(self) -> FilteredModelAdapter[AccountPayment]:
        return FilteredModelAdapter[AccountPayment](
            self.env, self._get_domain_adapter()
        )

    def _search(self, paging, params, order) -> tuple[int, AccountPayment]:
        return self.model_adapter.search_with_count(
            params.to_odoo_domain(self.env),
            limit=paging.limit,
            offset=paging.offset,
            order=order,
            context=params.to_odoo_context(self.env),
        )

    # -- creation (ported from pms_api_rest pms.transaction.service /
    #    pms.folio.do_payment) --

    @staticmethod
    def _problem(status_code, type_, title, detail):
        raise _PaymentProblem(
            JSONResponse(
                status_code=status_code,
                content={
                    "type": type_,
                    "title": title,
                    "status": status_code,
                    "detail": detail,
                },
                media_type="application/problem+json",
            )
        )

    def _not_found(self, detail):
        self._problem(404, "/errors/record-not-found", "Record not found", detail)

    def get(self, payment_id):
        try:
            payment = self.env["account.payment"].sudo().browse(payment_id).exists()
            if not payment:
                self._problem(
                    404,
                    "/errors/payment-not-found",
                    "Payment not found",
                    f"Payment {payment_id} does not exist.",
                )
            try:
                PmsBaseModel.pms_api_check_access(self.env.user, payment)
            except (AccessError, AccessDenied):
                self._problem(
                    404,
                    "/errors/payment-not-found",
                    "Payment not found",
                    f"Payment {payment_id} does not exist.",
                )
        except _PaymentProblem as problem:
            return problem.response
        return PaymentSummary.from_account_payment(payment)

    def _validation_error(self, detail):
        self._problem(422, "/errors/validation-error", "Validation error", detail)

    def _resolve_partner(self, partner_id):
        partner = self.env["res.partner"].sudo().browse(partner_id).exists()
        if not partner:
            self._not_found(f"Partner {partner_id} does not exist.")
        return partner

    def create_payment(self, payload: PaymentInput):
        try:
            if payload.folioId and payload.invoiceId:
                self._validation_error("folioId and invoiceId are mutually exclusive.")
            line = (
                self.env["account.payment.method.line"]
                .sudo()
                .browse(payload.paymentMethodId)
                .exists()
            )
            if not line:
                self._not_found(
                    f"Payment method {payload.paymentMethodId} does not exist."
                )
            journal = line.journal_id
            PmsBaseModel.pms_api_check_access(self.env.user, journal)
            payment_type, partner_type = _CREATE_TYPE_FIELDS[payload.paymentType]
            partner = (
                self._resolve_partner(payload.partnerId)
                if payload.partnerId
                else self.env["res.partner"]
            )
            if payload.paymentType == PaymentCreateType.supplierPayment and not partner:
                self._validation_error("supplierPayment requires partnerId.")

            if journal.type == "cash":
                self.env["account.bank.statement"].sudo()._pms_ensure_open_cash_session(
                    journal
                )

            if payload.paymentType == PaymentCreateType.customerPayment and (
                payload.folioId or payload.invoiceId
            ):
                payment = self._create_folio_payment(payload, line, partner)
            else:
                payment = self._create_simple_payment(
                    payload, line, partner, payment_type, partner_type
                )
        except _PaymentProblem as problem:
            return problem.response
        return PaymentSummary.from_account_payment(payment)

    def _resolve_context_folio(self, payload):
        """Return the folio for a customer payment context (folio or invoice)."""
        if payload.folioId:
            folio = self.env["pms.folio"].sudo().browse(payload.folioId).exists()
            if not folio:
                self._not_found(f"Folio {payload.folioId} does not exist.")
        else:
            invoice = self.env["account.move"].sudo().browse(payload.invoiceId).exists()
            if not invoice:
                self._not_found(f"Invoice {payload.invoiceId} does not exist.")
            folio = invoice.folio_ids[:1]
            if not folio:
                self._validation_error(
                    f"Invoice {payload.invoiceId} has no associated folio."
                )
        PmsBaseModel.pms_api_check_access(self.env.user, folio)
        return folio

    def _create_folio_payment(self, payload, line, partner):
        folio = self._resolve_context_folio(payload)
        partner = partner or folio.partner_id
        before = folio.payment_ids
        self.env["pms.folio"].sudo().do_payment(
            line,
            self.env.user,
            payload.amount,
            folio,
            partner=partner,
            date=payload.date,
            ref=payload.reference,
        )
        return folio.payment_ids - before

    def _create_simple_payment(
        self, payload, line, partner, payment_type, partner_type
    ):
        payment = (
            self.env["account.payment"]
            .sudo()
            .create(
                {
                    "journal_id": line.journal_id.id,
                    "payment_method_line_id": line.id,
                    "partner_id": partner.id if partner else False,
                    "amount": payload.amount,
                    "date": payload.date,
                    "ref": payload.reference,
                    "payment_type": payment_type,
                    "partner_type": partner_type,
                    "state": "draft",
                }
            )
        )
        payment.action_post()
        return payment

    def create_internal_transfer(self, payload: InternalTransferInput):
        try:
            if payload.originJournalId == payload.destinationJournalId:
                self._validation_error(
                    "El diario de origen y el de destino no pueden ser el mismo."
                )
            journals = self.env["account.journal"].sudo()
            origin = journals.browse(payload.originJournalId).exists()
            destination = journals.browse(payload.destinationJournalId).exists()
            if not origin:
                self._not_found(f"Journal {payload.originJournalId} does not exist.")
            if not destination:
                self._not_found(
                    f"Journal {payload.destinationJournalId} does not exist."
                )
            PmsBaseModel.pms_api_check_access(self.env.user, origin + destination)
            statement_model = self.env["account.bank.statement"].sudo()
            statement_model._pms_ensure_open_cash_session(origin)
            statement_model._pms_ensure_open_cash_session(destination)
            payment = (
                self.env["account.payment"]
                .sudo()
                .create(
                    {
                        "amount": payload.amount,
                        "journal_id": origin.id,
                        "date": payload.date,
                        "partner_id": origin.company_id.partner_id.id,
                        "ref": payload.reason,
                        "payment_type": "outbound",
                        "partner_type": "customer",
                        "is_internal_transfer": True,
                        "destination_journal_id": destination.id,
                        "partner_bank_id": destination.bank_account_id.id,
                    }
                )
            )
            payment.action_post()
        except _PaymentProblem as problem:
            return problem.response
        return PaymentSummary.from_account_payment(payment)
