from odoo import models
from odoo.osv import expression

from odoo.addons.pms_fastapi.dependencies import AuthenticatedEnv
from odoo.addons.pms_fastapi.models.fastapi_endpoint import pms_api_router
from odoo.addons.pms_fastapi.routers.payment import PaymentProblem
from odoo.addons.pms_fastapi.schemas.supplier_payment import (
    SupplierPaymentDetail,
    SupplierPaymentInput,
    SupplierPaymentUpdate,
)

HELPER = "pms_api_supplier_payment.supplier_payment_router.helper"


@pms_api_router.post(
    "/supplier-payments",
    response_model=SupplierPaymentDetail,
    status_code=201,
    tags=["payment"],
)
async def create_supplier_payment(
    env: AuthenticatedEnv,
    payload: SupplierPaymentInput,
) -> SupplierPaymentDetail:
    """Register a payment to a supplier.

    The account the money leaves from is derived from the payment method."""
    return env[HELPER].new().create_payment(payload)


@pms_api_router.get(
    "/supplier-payments/{payment_id}",
    response_model=SupplierPaymentDetail,
    tags=["payment"],
)
async def get_supplier_payment(
    env: AuthenticatedEnv,
    payment_id: int,
) -> SupplierPaymentDetail:
    """Get a supplier payment or refund in detail.

    Answers 404 for a payment that is not a supplier one."""
    return env[HELPER].new().get_detail(payment_id)


@pms_api_router.patch(
    "/supplier-payments/{payment_id}",
    response_model=SupplierPaymentDetail,
    tags=["payment"],
)
async def update_supplier_payment(
    env: AuthenticatedEnv,
    payment_id: int,
    payload: SupplierPaymentUpdate,
) -> SupplierPaymentDetail:
    """Partially update a registered supplier payment or refund.

    Only the modified fields are sent, and the payment keeps its `id`. To correct
    the payment method, cancel the payment and register a new one."""
    return env[HELPER].new().update_payment(payment_id, payload)


class PmsApiSupplierPaymentRouterHelper(models.AbstractModel):
    _name = HELPER
    _inherit = "pms_api_payment.payment_router.helper"
    _description = "PMS API Supplier Payment Router Helper"

    def _get_record_domain(self):
        res = super()._get_record_domain()
        own = [
            ("is_internal_transfer", "=", False),
            ("partner_type", "=", "supplier"),
        ]
        return expression.AND([res, own])

    def get_detail(self, payment_id):
        try:
            payment = self._resolve_payment_or_404(payment_id)
        except PaymentProblem as problem:
            return problem.response
        return SupplierPaymentDetail.from_account_payment(payment)

    def create_payment(self, payload: SupplierPaymentInput):
        try:
            # Savepoint so that a cash journal that auto-opened a session before
            # a later step failed does not leave a phantom empty session behind.
            with self.env.cr.savepoint():
                line = self._resolve_directed_payment_method_line(
                    payload.paymentMethodId, "outbound"
                )
                partner = self._resolve_partner(payload.partnerId)
                self._ensure_open_cash_session(line.journal_id)
                payment = self._create_simple_payment(
                    payload, line, partner, "outbound", "supplier"
                )
        except PaymentProblem as problem:
            return problem.response
        return SupplierPaymentDetail.from_account_payment(payment)

    def update_payment(self, payment_id, payload: SupplierPaymentUpdate):
        try:
            with self.env.cr.savepoint():
                payment = self._resolve_payment_or_404(payment_id)
                self._ensure_not_bank_matched(payment)
                self._update_partner_payment(payment, payload)
        except PaymentProblem as problem:
            return problem.response
        return SupplierPaymentDetail.from_account_payment(payment)
