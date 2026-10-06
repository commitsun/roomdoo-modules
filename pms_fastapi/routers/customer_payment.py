from odoo import _, models
from odoo.osv import expression

from odoo.addons.pms_fastapi.dependencies import AuthenticatedEnv
from odoo.addons.pms_fastapi.models.fastapi_endpoint import pms_api_router
from odoo.addons.pms_fastapi.routers.payment import PaymentProblem
from odoo.addons.pms_fastapi.schemas.base import PmsBaseModel
from odoo.addons.pms_fastapi.schemas.customer_payment import (
    CustomerPaymentDetail,
    CustomerPaymentInput,
    CustomerPaymentUpdate,
    PaymentRefundInput,
)

HELPER = "pms_api_customer_payment.customer_payment_router.helper"


@pms_api_router.post(
    "/customer-payments",
    response_model=CustomerPaymentDetail,
    status_code=201,
    tags=["payment"],
)
async def create_customer_payment(
    env: AuthenticatedEnv,
    payload: CustomerPaymentInput,
) -> CustomerPaymentDetail:
    """Register a customer payment.

    The account the money goes into is derived from the payment method. The
    payment may carry a folio or an invoice context (mutually exclusive): with an
    invoice it settles that invoice exactly, with a folio it is allocated across
    the folio."""
    return env[HELPER].new().create_payment(payload)


@pms_api_router.get(
    "/customer-payments/{payment_id}",
    response_model=CustomerPaymentDetail,
    tags=["payment"],
)
async def get_customer_payment(
    env: AuthenticatedEnv,
    payment_id: int,
) -> CustomerPaymentDetail:
    """Get a customer payment or refund in detail.

    Answers 404 for a payment that is not a customer one."""
    return env[HELPER].new().get_detail(payment_id)


@pms_api_router.patch(
    "/customer-payments/{payment_id}",
    response_model=CustomerPaymentDetail,
    tags=["payment"],
)
async def update_customer_payment(
    env: AuthenticatedEnv,
    payment_id: int,
    payload: CustomerPaymentUpdate,
) -> CustomerPaymentDetail:
    """Partially update a registered customer payment or refund.

    Only the modified fields are sent, and the payment keeps its `id`. If the
    payment settles an invoice, the settlement is recomputed automatically when
    the amount changes. To correct the payment method, cancel the payment and
    register a new one."""
    return env[HELPER].new().update_payment(payment_id, payload)


@pms_api_router.post(
    "/customer-payments/refunds",
    response_model=CustomerPaymentDetail,
    status_code=201,
    tags=["payment"],
)
async def create_customer_refund(
    env: AuthenticatedEnv,
    payload: PaymentRefundInput,
) -> CustomerPaymentDetail:
    """Refund one or more customer payments of the same folio.

    The whole operation is a single refund: one date, one payment method and one
    refund created for the total amount, applied to each original payment
    according to the per-line breakdown of the request. A payment that was
    already settled is not settled again; a note is left on the refund instead."""
    return env[HELPER].new().create_refund(payload)


class PmsApiCustomerPaymentRouterHelper(models.AbstractModel):
    _name = HELPER
    _inherit = "pms_api_payment.payment_router.helper"
    _description = "PMS API Customer Payment Router Helper"

    def _get_record_domain(self):
        res = super()._get_record_domain()
        own = [
            ("is_internal_transfer", "=", False),
            ("partner_type", "=", "customer"),
        ]
        return expression.AND([res, own])

    def get_detail(self, payment_id):
        try:
            payment = self._resolve_payment_or_404(payment_id)
        except PaymentProblem as problem:
            return problem.response
        return CustomerPaymentDetail.from_account_payment(payment)

    # -- creation (POST /customer-payments) --

    def create_payment(self, payload: CustomerPaymentInput):
        try:
            # Savepoint so that if a later step raises (e.g. a cash journal
            # auto-opens a session and then the folio/invoice resolution
            # fails), the already-created records — the phantom empty cash
            # session in particular — are rolled back instead of committed
            # alongside the error response.
            with self.env.cr.savepoint():
                if payload.folioId and payload.invoiceId:
                    self._validation_error(
                        _("folioId and invoiceId are mutually exclusive.")
                    )
                line = self._resolve_directed_payment_method_line(
                    payload.paymentMethodId, "inbound"
                )
                partner = (
                    self._resolve_partner(payload.partnerId)
                    if payload.partnerId
                    else self.env["res.partner"]
                )
                self._ensure_open_cash_session(line.journal_id)
                if payload.folioId or payload.invoiceId:
                    payment = self._create_context_payment(payload, line, partner)
                else:
                    payment = self._create_simple_payment(
                        payload, line, partner, "inbound", "customer"
                    )
        except PaymentProblem as problem:
            return problem.response
        return CustomerPaymentDetail.from_account_payment(payment)

    def _create_context_payment(self, payload, line, partner):
        """Register a customer payment from a folio or an invoice context.

        When the context is an invoice we know exactly which document the
        payment settles, so we register it against the invoice and let it
        reconcile deterministically. When it is a folio (no specific invoice)
        we fall back to the folio-level `do_payment`, whose reconciliation is
        best-effort (see pms_autoreconcile_folio_payments)."""
        if payload.invoiceId:
            return self._create_invoice_payment(payload, line)
        return self._create_folio_payment(payload, line, partner)

    def _resolve_folio(self, payload):
        folio = self.env["pms.folio"].sudo().browse(payload.folioId).exists()
        if not folio:
            self._not_found(_("Folio %s does not exist.") % payload.folioId)
        PmsBaseModel.pms_api_check_access(self.env.user, folio)
        return folio

    def _resolve_context_invoice(self, payload):
        invoice = self.env["account.move"].sudo().browse(payload.invoiceId).exists()
        if not invoice:
            self._not_found(_("Invoice %s does not exist.") % payload.invoiceId)
        folio = invoice.folio_ids[:1]
        if not folio:
            self._validation_error(
                _("Invoice %s has no associated folio.") % payload.invoiceId
            )
        # Access is granted through the folio, as in the folio-context path.
        PmsBaseModel.pms_api_check_access(self.env.user, folio)
        if invoice.state != "posted":
            self._validation_error(
                _("Invoice %s is not confirmed and cannot be paid.") % payload.invoiceId
            )
        return invoice

    def _create_folio_payment(self, payload, line, partner):
        folio = self._resolve_folio(payload)
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

    def _create_invoice_payment(self, payload, line):
        """Register the payment directly against the invoice.

        `account.payment.register` creates, posts and reconciles the payment
        against the invoice's receivable line in one shot. pms then recomputes
        `folio_ids` from `reconciled_invoice_ids`
        (account_payment._compute_folio_ids), so the folio link comes for free
        and the payment is left properly reconciled — unlike the folio-level
        `do_payment`, whose autoreconcile is heuristic and skips ambiguous
        matches."""
        invoice = self._resolve_context_invoice(payload)
        vals = {
            "amount": payload.amount,
            "payment_date": payload.date,
            "journal_id": line.journal_id.id,
            "payment_method_line_id": line.id,
        }
        if payload.reference:
            vals["communication"] = payload.reference
        return (
            self.env["account.payment.register"]
            .sudo()
            .with_context(active_model="account.move", active_ids=invoice.ids)
            .create(vals)
            ._create_payments()
        )

    # -- partial update (PATCH /customer-payments/{id}) --

    def update_payment(self, payment_id, payload: CustomerPaymentUpdate):
        try:
            with self.env.cr.savepoint():
                payment = self._resolve_payment_or_404(payment_id)
                self._ensure_not_bank_matched(payment)
                self._update_partner_payment(payment, payload)
        except PaymentProblem as problem:
            return problem.response
        return CustomerPaymentDetail.from_account_payment(payment)

    def _update_partner_payment(self, payment, payload):
        super()._update_partner_payment(payment, payload)
        # Re-posting posts the payment's own entry, not the invoice's, so the
        # folio<->invoice reconciliation is recomputed explicitly (the same
        # entry point do_payment uses).
        for move in payment.folio_ids.move_ids:
            move.sudo()._autoreconcile_folio_payments()

    # -- refunds (POST /customer-payments/refunds) --

    def _not_refundable(self, detail):
        self._problem(
            409,
            "/errors/payment-not-refundable",
            _("Payment not refundable"),
            detail,
        )

    def _resolve_refundable_payment(self, payment_id):
        """Resolve a payment that can be refunded: it must be a customer payment
        visible to the user (404 otherwise), and be an incoming, non-cancelled
        one — a refund cannot itself be refunded."""
        payment = self._resolve_payment_or_404(payment_id)
        if payment.payment_type != "inbound":
            self._not_refundable(
                _("Payment %s is not a refundable customer payment.") % payment_id
            )
        if payment.state != "posted":
            self._not_refundable(
                _("Payment %s is cancelled and cannot be refunded.") % payment_id
            )
        return payment

    def _ensure_refund_date_not_locked(self, company, refund_date):
        """The refund posts (and, when applicable, reconciles) an entry dated
        `refund_date`; a date inside a locked fiscal period is rejected, same
        rule as cancelling a payment."""
        lock_date = company._get_user_fiscal_lock_date()
        if refund_date and refund_date <= lock_date:
            self._problem(
                409,
                "/errors/fiscal-lock-date",
                _("Fiscal lock date"),
                _("The refund date falls within a locked fiscal period."),
            )

    def create_refund(self, payload: PaymentRefundInput):
        try:
            # Savepoint: if a later line fails validation (or posting/reconciling
            # raises), roll back the refund and any phantom cash session already
            # created instead of committing them alongside the error response.
            with self.env.cr.savepoint():
                method_line = self._resolve_directed_payment_method_line(
                    payload.paymentMethodId, "outbound"
                )
                journal = method_line.journal_id
                folio, origin_lines = self._resolve_refund_lines(payload)
                self._ensure_refund_date_not_locked(journal.company_id, payload.date)
                total = sum(amount for _payment, amount in origin_lines)
                self._ensure_open_cash_session(journal)
                refund = self._create_refund_payment(
                    payload, method_line, journal, folio, total
                )
                self._apply_refund_breakdown(refund, folio, origin_lines)
        except PaymentProblem as problem:
            return problem.response
        return CustomerPaymentDetail.from_account_payment(refund)

    def _resolve_refund_lines(self, payload):
        """Validate every line and return (folio, [(payment, amount), ...]).

        All payments must belong to the same folio, and each requested amount
        must fit the payment's available-to-refund amount."""
        folio = None
        origin_lines = []
        for line in payload.payments:
            payment = self._resolve_refundable_payment(line.paymentId)
            line_folio = payment.folio_ids[:1]
            if not line_folio:
                self._not_refundable(
                    _("Payment %s does not belong to a folio.") % line.paymentId
                )
            if folio is None:
                folio = line_folio
            elif line_folio != folio:
                self._not_refundable(_("All payments must belong to the same folio."))
            currency = payment.currency_id or payment.company_id.currency_id
            if (
                currency.compare_amounts(line.amount, payment.available_refund_amount)
                > 0
            ):
                self._problem(
                    409,
                    "/errors/refund-exceeds-available",
                    _("Refund exceeds available amount"),
                    _(
                        "The requested amount %(requested)s exceeds the amount "
                        "available to refund %(available)s of payment %(id)s."
                    )
                    % {
                        "requested": line.amount,
                        "available": payment.available_refund_amount,
                        "id": line.paymentId,
                    },
                )
            origin_lines.append((payment, line.amount))
        return folio, origin_lines

    def _create_refund_payment(self, payload, method_line, journal, folio, total):
        partner = folio.partner_id
        refund = (
            self.env["account.payment"]
            .sudo()
            .create(
                {
                    "journal_id": journal.id,
                    "payment_method_line_id": method_line.id,
                    "partner_id": partner.id if partner else False,
                    "amount": total,
                    "date": payload.date,
                    "payment_type": "outbound",
                    "partner_type": "customer",
                    "folio_ids": [(6, 0, folio.ids)],
                    "state": "draft",
                }
            )
        )
        refund.action_post()
        return refund

    def _receivable_line(self, payment):
        """The reconcilable line of a payment move on the partner receivable
        account (destination_account_id): a debit for the outbound refund, a
        credit for the inbound original payment."""
        return payment.move_id.line_ids.filtered(
            lambda mline: mline.account_id == payment.destination_account_id
        )[:1]

    def _apply_refund_breakdown(self, refund, folio, origin_lines):
        """Record the explicit refund->payment link per line and reconcile the
        refund against each original payment that is still fully open. A payment
        with any prior reconciliation is left untouched (only a chatter note),
        for administration to handle later (credit note, loss entry...)."""
        refund_line = self._receivable_line(refund)
        for payment, amount in origin_lines:
            payment_line = self._receivable_line(payment)
            reconcilable = bool(
                refund_line
                and payment_line
                and refund_line.account_id == payment_line.account_id
                and not payment_line.matched_debit_ids
                and not payment_line.matched_credit_ids
            )
            self.env["pms.payment.refund.line"].sudo().create(
                {
                    "refund_payment_id": refund.id,
                    "origin_payment_id": payment.id,
                    "amount": amount,
                    "is_reconciled": reconcilable,
                }
            )
            if reconcilable:
                self._reconcile_refund_line(refund_line, payment_line, amount)
            else:
                self._note_unreconciled_refund(refund, payment, amount)

    def _reconcile_refund_line(self, refund_line, payment_line, amount):
        """Reconcile exactly `amount` of the refund against the payment by
        creating the partial directly, so the per-line breakdown is respected
        even for partial refunds (plain reconcile() would greedily allocate the
        whole residual). Single-currency assumption; multi-currency is a known
        TO-REVIEW of this provisional refund system."""
        self.env["account.partial.reconcile"].sudo().create(
            {
                "debit_move_id": refund_line.id,
                "credit_move_id": payment_line.id,
                "amount": amount,
                "debit_amount_currency": amount,
                "credit_amount_currency": amount,
            }
        )

    def _note_unreconciled_refund(self, refund, payment, amount):
        refund.sudo().message_post(
            body=_(
                "Refund of %(amount)s originated from payment %(payment)s "
                "(folio %(folio)s), which was already reconciled, so it was NOT "
                "reconciled automatically. Administration must decide how to "
                "settle it (credit note, loss entry...)."
            )
            % {
                "amount": amount,
                "payment": payment.name or payment.id,
                "folio": payment.folio_ids[:1].name or "",
            }
        )
