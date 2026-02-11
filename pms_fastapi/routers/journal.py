from enum import Enum
from typing import Annotated

from fastapi import Depends, Query

from odoo import models
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
    helper = env["pms_api_journal.journal_router.helper"].new()
    journals = helper.search_journals(
        pms_property_id=pmsProperty,
        journal_type=journalType.value if journalType else None,
    )
    return [JournalSummary.from_account_journal(journal) for journal in journals]


class PmsApiJournalRouterHelper(models.AbstractModel):
    _name = "pms_api_journal.journal_router.helper"
    _description = "PMS API Journal Router Helper"

    def search_journals(self, pms_property_id=None, journal_type=None):
        domain = []
        if journal_type:
            domain.append(("type", "=", journal_type))
        if pms_property_id:
            domain = expression.AND(
                [
                    domain,
                    expression.OR(
                        [
                            [("pms_property_ids", "in", [pms_property_id])],
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
                            [
                                (
                                    "pms_property_ids",
                                    "in",
                                    self.env.user.pms_property_ids.ids,
                                )
                            ],
                            [("pms_property_ids", "=", False)],
                        ]
                    ),
                ]
            )
        return self.env["account.journal"].sudo().search(domain)
