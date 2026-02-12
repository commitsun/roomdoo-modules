from typing import Annotated

from fastapi import Depends

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
)
from odoo.addons.pms_fastapi.utils import FilteredModelAdapter

InvoiceOrderDependency = create_order_dependency(
    InvoiceOrderField, INVOICE_ORDER_MAPPING, ["name"]
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
