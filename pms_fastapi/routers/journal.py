from enum import Enum
from typing import Annotated

from fastapi import Query

from odoo import models
from odoo.osv import expression

from odoo.addons.pms_fastapi.dependencies import AuthenticatedEnv
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
    env: AuthenticatedEnv,
    pmsPropertyId: Annotated[
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
        pms_property_id=pmsPropertyId,
        journal_type=journalType.value if journalType else None,
    )
    return [JournalSummary.from_account_journal(journal) for journal in journals]


class PmsApiJournalRouterHelper(models.AbstractModel):
    _name = "pms_api_journal.journal_router.helper"
    _description = "PMS API Journal Router Helper"

    def search_journals(self, pms_property_id=None, journal_type=None):
        """Return journals allowed on PMS, scoped by property and type.

        ``journal_type`` accepts either a single ``str`` (HTTP layer) or an
        iterable of types — useful when callers know up front which journal
        types are relevant (e.g. payment methods only live on bank/cash
        journals, so callers can skip sale/purchase/general entirely).
        """
        domain = [("allowed_on_pms", "=", True)]
        if journal_type:
            if isinstance(journal_type, str):
                domain.append(("type", "=", journal_type))
            else:
                domain.append(("type", "in", list(journal_type)))
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
