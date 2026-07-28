from typing import Annotated

from fastapi import Depends, Query
from fastapi.responses import JSONResponse, Response

from odoo import SUPERUSER_ID, _, models
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
    PaymentOrderField,
    PaymentSearch,
    PaymentSummary,
    ReportFormatEnum,
)
from odoo.addons.pms_fastapi.utils import (
    ApiProblem,
    FilteredModelAdapter,
    build_problem,
)

PaymentOrderDependency = create_order_dependency(
    PaymentOrderField, PAYMENT_ORDER_MAPPING, ["-date"]
)

PAYMENT_REPORT_MAX_RECORDS = 5000


class PaymentProblem(ApiProblem):
    """Problem raised by any of the payment helpers, caught by their callers.

    Shared by the ledger router and the per-type entity routers
    (customer_payment, supplier_payment, internal_transfer), which all inherit
    the base helper below.
    """


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
    """Get a single payment of any type, as it appears in the listing.

    Useful when only the id is known (a deep link, a refresh): the `paymentType`
    of the response tells which entity holds its full representation
    (/customer-payments, /supplier-payments, /internal-transfers)."""
    return env["pms_api_payment.payment_router.helper"].new().get(payment_id)


@pms_api_router.get(
    "/payments/{payment_id}/report",
    tags=["payment"],
    responses={
        200: {"content": {"application/pdf": {}}},
    },
    response_class=Response,
)
async def get_payment_report(
    env: AuthenticatedEnv,
    payment_id: int,
) -> Response:
    """Download the PDF report for a specific payment."""
    return (
        env["pms_api_payment.payment_router.helper"].new().get_payment_pdf(payment_id)
    )


@pms_api_router.post(
    "/payments/report",
    tags=["payment"],
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
async def payments_report(
    env: AuthenticatedEnv,
    report_format: Annotated[
        ReportFormatEnum,
        Query(alias="format", description="Output file format."),
    ],
    filters: Annotated[PaymentSearch, Depends()],
    ids: Annotated[
        list[int] | None,
        Query(
            description="Payment IDs to include in the report. "
            "Mutually exclusive with filter parameters.",
        ),
    ] = None,
) -> Response:
    """Generate and download a payments report in PDF or Excel format.

    Pass either an explicit list of `ids` or filter parameters (the same ones
    accepted by GET /payments). They are mutually exclusive."""
    return (
        env["pms_api_payment.payment_router.helper"]
        .new()
        ._generate_report(report_format, filters, ids)
    )


@pms_api_router.post(
    "/payments/{payment_id}/cancel",
    response_model=PaymentSummary,
    tags=["payment"],
)
async def cancel_payment(
    env: AuthenticatedEnv,
    payment_id: int,
) -> PaymentSummary:
    """Cancel a registered payment of any type.

    Cancelling is a state transition (not a deletion): the related accounting
    entry is reversed. It is blocked when the payment date falls within a
    locked fiscal period."""
    return env["pms_api_payment.payment_router.helper"].new().cancel_payment(payment_id)


class PmsApiPaymentRouterHelper(models.AbstractModel):
    """Base helper of every payment router.

    Holds what does not depend on the payment type: the listing and the reports
    of the cross-type ledger, the cancellation, and the shared building blocks
    the per-type entity helpers reuse (record resolution, payment method
    resolution, cash sessions, posted-payment edition).
    """

    _name = "pms_api_payment.payment_router.helper"
    _description = "PMS API Payment Router Helper"

    def _get_domain_adapter(self):
        # Posted payments only. Internal transfers are stored as two paired
        # account.payment records (inbound + outbound) and BOTH legs are
        # returned, replicating the legacy API behaviour (no dedup).
        return [("state", "=", "posted")]

    def _get_record_domain(self):
        """Domain narrowing the resolution of a single payment by id.

        Empty here: the ledger addresses payments of any type. Each entity
        helper overrides it with its own type, so that reaching a payment of
        another type through it answers 404 instead of a representation with
        half its fields empty.

        Kept apart from `_get_domain_adapter()` on purpose: that one also
        filters `state = posted` (the listing/report scope) and applying it to
        item resolution would turn an already cancelled payment into a 404.
        """
        return []

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

    # -- problems --

    @staticmethod
    def _problem(status_code, type_, title, detail):
        raise PaymentProblem(build_problem(status_code, type_, title, detail))

    def _not_found(self, detail):
        self._problem(404, "/errors/record-not-found", _("Record not found"), detail)

    def _validation_error(self, detail):
        self._problem(422, "/errors/validation-error", _("Validation error"), detail)

    # -- record resolution --

    def get(self, payment_id):
        try:
            payment = self._resolve_payment_or_404(payment_id)
        except PaymentProblem as problem:
            return problem.response
        return PaymentSummary.from_account_payment(payment)

    def _resolve_payment_or_404(self, payment_id):
        payment = (
            self.env["account.payment"]
            .sudo()
            .browse(payment_id)
            .exists()
            .filtered_domain(self._get_record_domain())
        )
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
        return payment

    def _resolve_partner(self, partner_id):
        partner = self.env["res.partner"].sudo().browse(partner_id).exists()
        if not partner:
            self._not_found(_("Partner %s does not exist.") % partner_id)
        return partner

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

    def _resolve_directed_payment_method_line(self, payment_method_id, payment_type):
        """Resolve a payment method line that must move the money in a given
        direction (inbound to collect, outbound to pay out)."""
        line = self._resolve_payment_method_line(payment_method_id)
        if line.payment_type != payment_type:
            detail = (
                _("Payment method %s does not accept incoming payments.")
                if payment_type == "inbound"
                else _("Payment method %s does not accept outgoing payments.")
            )
            self._validation_error(detail % payment_method_id)
        return line

    def get_payment_pdf(self, payment_id):
        try:
            payment = self._resolve_payment_or_404(payment_id)
        except PaymentProblem as problem:
            return problem.response
        content, _report_type = (
            self.env["ir.actions.report"]
            .sudo()
            ._render_qweb_pdf("account.action_report_payment_receipt", [payment.id])
        )
        filename = "%s.pdf" % (payment.name or _("payment")).replace("/", "-")
        return Response(
            content=content,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
            },
        )

    # -- report (POST /payments/report) --

    @staticmethod
    def _has_report_filters(filters):
        return any(v is not None for v in filters.__dict__.values())

    def _resolve_report_payments(self, filters, ids):
        if ids and self._has_report_filters(filters):
            return (
                JSONResponse(
                    status_code=400,
                    content={
                        "type": "/errors/mutually-exclusive-params",
                        "title": _("Mutually exclusive parameters"),
                        "status": 400,
                        "detail": _(
                            "Cannot specify both 'ids' and filter parameters. "
                            "Use one or the other."
                        ),
                    },
                    media_type="application/problem+json",
                ),
                None,
            )
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
        if count > PAYMENT_REPORT_MAX_RECORDS:
            return (
                JSONResponse(
                    status_code=400,
                    content={
                        "type": "/errors/record-limit-exceeded",
                        "title": _("Record limit exceeded"),
                        "status": 400,
                        "detail": _(
                            "The export requested %s records, "
                            "but the maximum allowed is %s."
                        )
                        % (count, PAYMENT_REPORT_MAX_RECORDS),
                        "requestedCount": count,
                        "maxAllowed": PAYMENT_REPORT_MAX_RECORDS,
                    },
                    media_type="application/problem+json",
                ),
                None,
            )
        payments = (
            self.model_adapter.search(domain)
            if context is None
            else self.model_adapter.search(domain, context=context)
        )
        return None, payments

    def _generate_report(self, report_format, filters, ids):
        error, payments = self._resolve_report_payments(filters, ids)
        if error is not None:
            return error
        if report_format == ReportFormatEnum.xlsx:
            return self._render_payments_xlsx(payments)
        return self._render_payments_pdf(payments)

    def _render_payments_pdf(self, payments):
        content, _report_type = (
            self.env["ir.actions.report"]
            .sudo()
            ._render_qweb_pdf("roomdoo_payments_exporter.report_payments", payments.ids)
        )
        return Response(
            content=content,
            media_type="application/pdf",
            headers={
                "Content-Disposition": 'attachment; filename="payments_report.pdf"',
            },
        )

    def _render_payments_xlsx(self, payments):
        # `report_xlsx._render_xlsx` forces `.sudo(False)` on the report model,
        # so `.sudo()` here would not bypass ACL inside the export.
        content, _report_type = (
            self.env["ir.actions.report"]
            .with_user(SUPERUSER_ID)
            ._render("roomdoo_payments_exporter.payment_report", payments.ids)
        )
        return Response(
            content=content,
            media_type=(
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ),
            headers={
                "Content-Disposition": 'attachment; filename="payments_report.xlsx"',
            },
        )

    # -- cancellation (POST /payments/{id}/cancel) --

    def _ensure_not_locked(self, payments):
        """A payment whose date falls within a locked fiscal period cannot be
        cancelled: resetting it to draft would modify a locked entry. The
        cancellation runs as superuser, so the effective lock date is the one
        Odoo will enforce on the draft transition."""
        for payment in payments:
            lock_date = payment.company_id._get_user_fiscal_lock_date()
            if payment.date and payment.date <= lock_date:
                self._problem(
                    409,
                    "/errors/fiscal-lock-date",
                    _("Fiscal lock date"),
                    _(
                        "The payment date falls within a locked fiscal period "
                        "and cannot be cancelled."
                    ),
                )

    def cancel_payment(self, payment_id):
        try:
            payment = self._resolve_payment_or_404(payment_id)
            # An internal transfer is two paired payments reconciled against
            # each other; both legs must move together.
            legs = payment
            if payment.is_internal_transfer:
                legs = payment + payment.paired_internal_transfer_payment_id
            self._ensure_not_bank_matched(payment)
            self._ensure_not_locked(legs)
            for leg in legs:
                self._ensure_open_cash_session(leg.journal_id)
            # draft first (un-reconciles and reopens the entry), then cancel.
            legs.action_draft()
            legs.action_cancel()
        except PaymentProblem as problem:
            return problem.response
        return PaymentSummary.from_account_payment(payment)

    # -- creation building blocks, shared by the entity helpers --

    def _ensure_open_cash_session(self, journal):
        if journal.type == "cash":
            self.env["account.bank.statement"].sudo()._pms_ensure_open_cash_session(
                journal
            )

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

    # -- edition building blocks, shared by the entity helpers --

    def _ensure_not_bank_matched(self, payment):
        """A payment reconciled against a bank statement cannot be modified:
        editing it would break the bank reconciliation. Internal transfers move
        both legs together, so the counterpart is checked as well."""
        legs = payment + payment.paired_internal_transfer_payment_id
        if any(legs.mapped("is_matched")):
            self._problem(
                409,
                "/errors/payment-bank-matched",
                _("Payment cannot be modified"),
                _(
                    "This payment is reconciled against a bank statement and "
                    "cannot be modified."
                ),
            )

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

    def _partner_payment_update_vals(self, payment, payload):
        """Vals of a payment that has a contact (customer or supplier, as
        opposed to an internal transfer): the common ones plus contact and
        reference."""
        vals = self._common_update_vals(payment, payload)
        if payload.partnerId is not None and payload.partnerId != payment.partner_id.id:
            vals["partner_id"] = self._resolve_partner(payload.partnerId).id
        if payload.reference is not None and payload.reference != (payment.ref or ""):
            vals["ref"] = payload.reference
        return vals

    def _update_partner_payment(self, payment, payload):
        """Apply a customer/supplier payment edition in place.

        The payment method is deliberately not editable (it is not part of the
        update schemas): changing it to a method of another account is
        impossible on an accounting entry that has been posted once, and faking
        it by cancelling and re-creating the payment would change the id of the
        resource a PATCH is supposed to be editing. Such a correction is a
        cancellation plus a new registration, and the API says so."""
        vals = self._partner_payment_update_vals(payment, payload)
        if not vals:
            return
        self._ensure_open_cash_session(payment.journal_id)
        payment.action_draft()
        payment.write(vals)
        payment.action_post()
