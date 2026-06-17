from typing import Annotated

from fastapi import Depends
from fastapi.responses import JSONResponse

from odoo import _, models
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
    PaymentUpdate,
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


@pms_api_router.patch(
    "/payments/{payment_id}",
    response_model=PaymentSummary,
    tags=["payment"],
)
async def update_payment(
    env: AuthenticatedEnv,
    payment_id: int,
    payload: PaymentUpdate,
) -> PaymentSummary:
    """Partially update a registered payment (amount, date, payment method).

    Only the modified fields are sent. If the payment is reconciled against an
    invoice, the reconciliation is recomputed automatically when the amount
    changes."""
    return (
        env["pms_api_payment.payment_router.helper"]
        .new()
        .update_payment(payment_id, payload)
    )


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
        self._problem(404, "/errors/record-not-found", _("Record not found"), detail)

    def get(self, payment_id):
        try:
            payment = self.env["account.payment"].sudo().browse(payment_id).exists()
            if not payment:
                self._problem(
                    404,
                    "/errors/payment-not-found",
                    _("Payment not found"),
                    _("Payment %s does not exist.") % payment_id,
                )
            try:
                PmsBaseModel.pms_api_check_access(self.env.user, payment)
            except (AccessError, AccessDenied):
                self._problem(
                    404,
                    "/errors/payment-not-found",
                    _("Payment not found"),
                    _("Payment %s does not exist.") % payment_id,
                )
        except _PaymentProblem as problem:
            return problem.response
        return PaymentSummary.from_account_payment(payment)

    def _validation_error(self, detail):
        self._problem(422, "/errors/validation-error", _("Validation error"), detail)

    def _resolve_partner(self, partner_id):
        partner = self.env["res.partner"].sudo().browse(partner_id).exists()
        if not partner:
            self._not_found(_("Partner %s does not exist.") % partner_id)
        return partner

    def create_payment(self, payload: PaymentInput):
        try:
            if payload.folioId and payload.invoiceId:
                self._validation_error(
                    _("folioId and invoiceId are mutually exclusive.")
                )
            line = (
                self.env["account.payment.method.line"]
                .sudo()
                .browse(payload.paymentMethodId)
                .exists()
            )
            if not line:
                self._not_found(
                    _("Payment method %s does not exist.") % payload.paymentMethodId
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
                self._validation_error(_("supplierPayment requires partnerId."))

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
                self._not_found(_("Folio %s does not exist.") % payload.folioId)
        else:
            invoice = self.env["account.move"].sudo().browse(payload.invoiceId).exists()
            if not invoice:
                self._not_found(_("Invoice %s does not exist.") % payload.invoiceId)
            folio = invoice.folio_ids[:1]
            if not folio:
                self._validation_error(
                    _("Invoice %s has no associated folio.") % payload.invoiceId
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
                    _("Origin and destination journals cannot be the same.")
                )
            journals = self.env["account.journal"].sudo()
            origin = journals.browse(payload.originJournalId).exists()
            destination = journals.browse(payload.destinationJournalId).exists()
            if not origin:
                self._not_found(
                    _("Journal %s does not exist.") % payload.originJournalId
                )
            if not destination:
                self._not_found(
                    _("Journal %s does not exist.") % payload.destinationJournalId
                )
            invalid = (origin + destination).filtered(
                lambda journal: journal.type not in ("bank", "cash")
            )
            if invalid:
                self._validation_error(
                    _(
                        "Internal transfers only allow bank or cash journals. "
                        "Invalid journals: %s."
                    )
                    % ", ".join(invalid.mapped("display_name"))
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

    # -- partial update (PATCH /payments/{id}) --

    def update_payment(self, payment_id, payload: PaymentUpdate):
        try:
            payment = self.env["account.payment"].sudo().browse(payment_id).exists()
            if not payment:
                self._not_found(_("Payment %s does not exist.") % payment_id)
            try:
                PmsBaseModel.pms_api_check_access(self.env.user, payment)
            except (AccessError, AccessDenied):
                self._not_found(_("Payment %s does not exist.") % payment_id)
            if payment.is_internal_transfer:
                self._update_internal_transfer(payment, payload)
            else:
                # A journal change recreates the record (see below), so use the
                # returned payment for the response.
                payment = self._update_simple_payment(payment, payload)
        except _PaymentProblem as problem:
            return problem.response
        return PaymentSummary.from_account_payment(payment)

    def _resolve_payment_method_line(self, payment_method_id):
        line = (
            self.env["account.payment.method.line"]
            .sudo()
            .browse(payment_method_id)
            .exists()
        )
        if not line:
            self._not_found(_("Payment method %s does not exist.") % payment_method_id)
        PmsBaseModel.pms_api_check_access(self.env.user, line.journal_id)
        return line

    def _common_update_vals(self, payment, payload):
        """Vals for the fields shared by every payment type (amount, date)."""
        vals = {}
        currency = payment.currency_id or payment.company_id.currency_id
        if (
            payload.amount is not None
            and currency.compare_amounts(payload.amount, payment.amount) != 0
        ):
            vals["amount"] = payload.amount
        if payload.date is not None and payload.date != payment.date:
            vals["date"] = payload.date
        return vals

    def _ensure_open_cash_session(self, journal):
        if journal.type == "cash":
            self.env["account.bank.statement"].sudo()._pms_ensure_open_cash_session(
                journal
            )

    def _replace_payment_for_journal_change(self, payment, vals, journal):
        """Odoo forbids changing the journal of an already-posted payment, so a
        payment-method change that moves to another journal cancels the original
        and recreates it (same approach as the legacy API). The id changes."""
        self._ensure_open_cash_session(journal)
        payment.action_draft()
        payment.action_cancel()
        new_payment = payment.copy({"folio_ids": [(6, 0, payment.folio_ids.ids)]})
        new_payment.write(dict(vals, journal_id=journal.id))
        new_payment.action_post()
        return new_payment

    def _update_simple_payment(self, payment, payload):
        vals = self._common_update_vals(payment, payload)
        new_journal = None
        if payload.paymentMethodId is not None:
            line = self._resolve_payment_method_line(payload.paymentMethodId)
            if line.id != payment.payment_method_line_id.id:
                vals["payment_method_line_id"] = line.id
                if line.journal_id.id != payment.journal_id.id:
                    new_journal = line.journal_id
        if not vals:
            return payment
        if new_journal:
            payment = self._replace_payment_for_journal_change(
                payment, vals, new_journal
            )
        else:
            self._ensure_open_cash_session(payment.journal_id)
            payment.action_draft()
            payment.write(vals)
            payment.action_post()
        # Re-posting posts the payment's own entry, not the invoice's, so the
        # folio<->invoice reconciliation is recomputed explicitly (the same
        # entry point do_payment uses).
        for move in payment.folio_ids.move_ids:
            move.sudo()._autoreconcile_folio_payments()
        return payment

    def _update_internal_transfer(self, payment, payload):
        # An internal transfer has two journals (origin + destination), so the
        # single 'payment mode' doesn't apply to it.
        if payload.paymentMethodId is not None:
            self._validation_error(
                _("paymentMethodId does not apply to an internal transfer.")
            )
        # Both legs share amount and date; edit them together to keep the pair
        # consistent (they are reconciled against each other).
        counterpart = payment.paired_internal_transfer_payment_id
        legs = payment + counterpart
        vals = self._common_update_vals(payment, payload)
        if not vals:
            return
        for leg in legs:
            self._ensure_open_cash_session(leg.journal_id)
        legs.action_draft()
        legs.write(vals)
        legs.action_post()
        # action_post won't re-pair (the pairing already exists), so the two
        # transfer lines stay unreconciled until we reconcile them again.
        lines = (payment.move_id.line_ids + counterpart.move_id.line_ids).filtered(
            lambda line: line.account_id == payment.destination_account_id
            and not line.reconciled
        )
        lines.reconcile()
