from enum import Enum
from typing import Annotated

from fastapi import Depends, Query

from odoo.api import Environment
from odoo.osv import expression

from odoo.addons.fastapi_auth_jwt.dependencies import AuthJwtOdooEnv
from odoo.addons.pms_fastapi.models.fastapi_endpoint import pms_api_router
from odoo.addons.pms_fastapi.schemas.journal import JournalSummary


class JournalType(str, Enum):
    sale = "sale"
    purchase = "purchase"
    cash = "cash"
    bank = "bank"
    general = "general"


@pms_api_router.get(
    "/journals",
    response_model=list[JournalSummary],
    tags=["account"],
)
async def list_journals(
    env: Annotated[Environment, Depends(AuthJwtOdooEnv(validator_name="api_pms"))],
    pmsProperty: Annotated[
        int | None,
        Query(description="Filter journals of the given property."),
    ] = None,
    journalType: Annotated[
        JournalType | None,
        Query(description="Filter by journal type."),
    ] = None,
) -> list[JournalSummary]:
    """List journals, optionally filtered by type and property."""
    domain = []
    if journalType:
        domain.append(("type", "=", journalType.value))
    if pmsProperty:
        domain = expression.AND(
            [
                domain,
                expression.OR(
                    [
                        [("pms_property_ids", "in", [pmsProperty])],
                        [("pms_property_ids", "=", False)],
                    ]
                ),
            ]
        )
    else:
        domain = expression.AND(
            [
                domain,
                expression.OR(
                    [
                        [("pms_property_ids", "in", env.user.pms_property_ids.ids)],
                        [("pms_property_ids", "=", False)],
                    ]
                ),
            ]
        )
    journals = env["account.journal"].sudo().search(domain)
    return [JournalSummary.from_account_journal(journal) for journal in journals]
