from typing import Annotated

from fastapi import Query

from odoo import models

from odoo.addons.pms_fastapi.dependencies import AuthenticatedEnv
from odoo.addons.pms_fastapi.models.fastapi_endpoint import pms_api_router
from odoo.addons.pms_fastapi.schemas.payment_method import PaymentMethodSummary


@pms_api_router.get(
    "/payment-methods",
    response_model=list[PaymentMethodSummary],
    tags=["account"],
)
async def list_payment_methods(
    env: AuthenticatedEnv,
    pmsPropertyId: Annotated[
        int | None,
        Query(
            description="Restrict to payment methods whose journal is "
            "available for the given property."
        ),
    ] = None,
) -> list[PaymentMethodSummary]:
    """List inbound payment methods allowed on PMS, scoped through their journal."""
    helper = env["pms_api_payment_method.payment_method_router.helper"].new()
    methods = helper.search_payment_methods(pms_property_id=pmsPropertyId)
    return [
        PaymentMethodSummary.from_account_payment_method_line(method)
        for method in methods
    ]


class PmsApiPaymentMethodRouterHelper(models.AbstractModel):
    _name = "pms_api_payment_method.payment_method_router.helper"
    _description = "PMS API Payment Method Router Helper"

    def search_payment_methods(self, pms_property_id=None):
        # Payment method lines only exist on bank and cash journals; pass the
        # types down so the journal helper skips sale/purchase/general.
        journals = (
            self.env["pms_api_journal.journal_router.helper"]
            .new()
            .search_journals(
                pms_property_id=pms_property_id,
                journal_type=("bank", "cash"),
            )
        )
        return (
            self.env["account.payment.method.line"]
            .sudo()
            .search(
                [
                    ("payment_type", "=", "inbound"),
                    ("allowed_on_pms", "=", True),
                    ("journal_id", "in", journals.ids),
                ]
            )
        )
