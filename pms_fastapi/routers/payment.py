from typing import Annotated

from fastapi import Depends

from odoo import models

from odoo.addons.account.models.account_payment import AccountPayment
from odoo.addons.extendable_fastapi.schemas import PagedCollection
from odoo.addons.fastapi.dependencies import paging
from odoo.addons.fastapi.schemas import Paging
from odoo.addons.pms_fastapi.dependencies import (
    AuthenticatedEnv,
    create_order_dependency,
)
from odoo.addons.pms_fastapi.models.fastapi_endpoint import pms_api_router
from odoo.addons.pms_fastapi.schemas.payment import (
    PAYMENT_ORDER_MAPPING,
    PaymentOrderField,
    PaymentSearch,
    PaymentSummary,
)
from odoo.addons.pms_fastapi.utils import FilteredModelAdapter

PaymentOrderDependency = create_order_dependency(
    PaymentOrderField, PAYMENT_ORDER_MAPPING, ["-date"]
)


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
