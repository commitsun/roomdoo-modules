from odoo import _, models
from odoo.osv import expression

from odoo.addons.pms_fastapi.dependencies import AuthenticatedEnv
from odoo.addons.pms_fastapi.models.fastapi_endpoint import pms_api_router
from odoo.addons.pms_fastapi.routers.payment import PaymentProblem
from odoo.addons.pms_fastapi.schemas.internal_transfer import (
    InternalTransferDetail,
    InternalTransferInput,
    InternalTransferUpdate,
)

HELPER = "pms_api_internal_transfer.internal_transfer_router.helper"


@pms_api_router.post(
    "/internal-transfers",
    response_model=InternalTransferDetail,
    status_code=201,
    tags=["payment"],
)
async def create_internal_transfer(
    env: AuthenticatedEnv,
    payload: InternalTransferInput,
) -> InternalTransferDetail:
    """Register an internal transfer between two payment methods.

    Takes an outbound origin and an inbound destination payment method; the
    accounts the money moves between are derived from them and must differ."""
    return env[HELPER].new().create_transfer(payload)


@pms_api_router.get(
    "/internal-transfers/{transfer_id}",
    response_model=InternalTransferDetail,
    tags=["payment"],
)
async def get_internal_transfer(
    env: AuthenticatedEnv,
    transfer_id: int,
) -> InternalTransferDetail:
    """Get an internal transfer in detail, with both ends of the movement.

    Answers 404 for a payment that is not an internal transfer."""
    return env[HELPER].new().get_detail(transfer_id)


@pms_api_router.patch(
    "/internal-transfers/{transfer_id}",
    response_model=InternalTransferDetail,
    tags=["payment"],
)
async def update_internal_transfer(
    env: AuthenticatedEnv,
    transfer_id: int,
    payload: InternalTransferUpdate,
) -> InternalTransferDetail:
    """Partially update a registered internal transfer.

    Only the modified fields are sent, and they are applied to both ends of the
    movement at once so that origin and destination stay consistent."""
    return env[HELPER].new().update_transfer(transfer_id, payload)


class PmsApiInternalTransferRouterHelper(models.AbstractModel):
    _name = HELPER
    _inherit = "pms_api_payment.payment_router.helper"
    _description = "PMS API Internal Transfer Router Helper"

    def _get_record_domain(self):
        res = super()._get_record_domain()
        return expression.AND([res, [("is_internal_transfer", "=", True)]])

    def _resolve_payment_or_404(self, payment_id):
        """Resolve a transfer through its origin (outbound) leg.

        A transfer is stored as two paired payments and either id addresses the
        same operation, but only the outbound one knows both ends of the movement
        (the account it left and the account it went to), so it is the canonical
        record of the resource.
        """
        payment = super()._resolve_payment_or_404(payment_id)
        if payment.payment_type == "inbound":
            payment = payment.paired_internal_transfer_payment_id
        return payment

    def get_detail(self, transfer_id):
        try:
            transfer = self._resolve_payment_or_404(transfer_id)
        except PaymentProblem as problem:
            return problem.response
        return InternalTransferDetail.from_account_payment(transfer)

    # -- creation (POST /internal-transfers) --

    def create_transfer(self, payload: InternalTransferInput):
        try:
            # Savepoint: two cash sessions may be auto-opened before a later
            # step fails, and they must not survive the error response.
            with self.env.cr.savepoint():
                # Payment method lines only exist on bank/cash journals, so
                # resolving the journal from the line implicitly guarantees a
                # valid journal type.
                origin_line = self._resolve_directed_payment_method_line(
                    payload.originPaymentMethodId, "outbound"
                )
                destination_line = self._resolve_directed_payment_method_line(
                    payload.destinationPaymentMethodId, "inbound"
                )
                origin = origin_line.journal_id
                destination = destination_line.journal_id
                if origin == destination:
                    self._validation_error(
                        _("Origin and destination accounts cannot be the same.")
                    )
                self._ensure_open_cash_session(origin)
                self._ensure_open_cash_session(destination)
                transfer = (
                    self.env["account.payment"]
                    .sudo()
                    .create(
                        {
                            "amount": payload.amount,
                            "journal_id": origin.id,
                            "payment_method_line_id": origin_line.id,
                            "date": payload.date,
                            "partner_id": origin.company_id.partner_id.id,
                            "ref": payload.reference,
                            "payment_type": "outbound",
                            "partner_type": "customer",
                            "is_internal_transfer": True,
                            "destination_journal_id": destination.id,
                            "partner_bank_id": destination.bank_account_id.id,
                        }
                    )
                )
                transfer.action_post()
                self._apply_destination_method_line(transfer, destination_line)
        except PaymentProblem as problem:
            return problem.response
        return InternalTransferDetail.from_account_payment(transfer)

    def _apply_destination_method_line(self, transfer, destination_line):
        """Force the chosen inbound method line onto the auto-created counterpart.

        Odoo pairs the transfer on post and assigns the destination journal's
        default inbound line. When the front picked a different one, reassign it.
        An internal transfer only reconciles its two legs against each other (no
        invoice/folio reconciliation), so re-posting the counterpart is contained:
        we just re-reconcile the pair afterwards (same as update_transfer).
        """
        counterpart = transfer.paired_internal_transfer_payment_id
        if counterpart.payment_method_line_id == destination_line:
            return
        counterpart.action_draft()
        counterpart.write({"payment_method_line_id": destination_line.id})
        counterpart.action_post()
        self._reconcile_legs(transfer, counterpart)

    def _reconcile_legs(self, transfer, counterpart):
        """Reconcile the two legs of the transfer against each other.

        Posting does not re-pair an already paired transfer, so after any
        re-post the two transfer lines stay open until reconciled again."""
        lines = (transfer.move_id.line_ids + counterpart.move_id.line_ids).filtered(
            lambda line: line.account_id == transfer.destination_account_id
            and not line.reconciled
        )
        lines.reconcile()

    # -- partial update (PATCH /internal-transfers/{id}) --

    def update_transfer(self, transfer_id, payload: InternalTransferUpdate):
        try:
            with self.env.cr.savepoint():
                transfer = self._resolve_payment_or_404(transfer_id)
                self._ensure_not_bank_matched(transfer)
                self._update_legs(transfer, payload)
        except PaymentProblem as problem:
            return problem.response
        return InternalTransferDetail.from_account_payment(transfer)

    def _update_legs(self, transfer, payload):
        # Both legs share amount, date and reference; edit them together to keep
        # the pair consistent (they are reconciled against each other).
        counterpart = transfer.paired_internal_transfer_payment_id
        legs = transfer + counterpart
        vals = self._common_update_vals(transfer, payload)
        if payload.reference is not None and payload.reference != (transfer.ref or ""):
            vals["ref"] = payload.reference
        if not vals:
            return
        for leg in legs:
            self._ensure_open_cash_session(leg.journal_id)
        legs.action_draft()
        legs.write(vals)
        legs.action_post()
        self._reconcile_legs(transfer, counterpart)
