from enum import Enum
from typing import Annotated

from fastapi import Query

from odoo import models

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
        list[JournalType] | None,
        Query(description="Filter by journal type. Repeat to filter by several."),
    ] = None,
) -> list[JournalSummary]:
    """List journals, optionally filtered by type and property."""
    helper = env["pms_api_journal.journal_router.helper"].new()
    journals = helper.search_journals(
        pms_property_id=pmsPropertyId,
        journal_type=[t.value for t in journalType] if journalType else None,
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
        # A journal with no property is NOT returned. Elsewhere in PMS an empty
        # pms_property_ids conventionally means "every property", but a generic
        # company journal is not something a hotel should be offered: nobody at
        # reception is meant to pick it, and the listings already dropped them
        # one by one after the fact (see the filtered() calls in
        # schemas/payment.py and in pms_api_rest's journal and transaction
        # services). Excluding them here is what finally makes the selector and
        # the listing agree.
        property_ids = (
            [pms_property_id] if pms_property_id else self.env.user.pms_property_ids.ids
        )
        domain.append(("pms_property_ids", "in", property_ids))
        return self.env["account.journal"].sudo().search(domain)
