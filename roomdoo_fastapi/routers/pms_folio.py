from typing import Annotated

from fastapi import Depends

from odoo.addons.extendable_fastapi.schemas import PagedCollection
from odoo.addons.fastapi.dependencies import paging
from odoo.addons.fastapi.schemas import Paging
from odoo.addons.pms_fastapi.dependencies import AuthenticatedEnv
from odoo.addons.pms_fastapi.models.fastapi_endpoint import pms_api_router
from odoo.addons.pms_fastapi.routers.pms_folio import folio_order
from odoo.addons.pms_fastapi.schemas.pms_folio import FolioCountSummary, FolioSummary
from odoo.addons.roomdoo_fastapi.schemas.pms_folio import FolioPendingSearch


@pms_api_router.get(
    "/folios/pending-closure",
    response_model=PagedCollection[FolioSummary],
    tags=["folio"],
)
async def list_folios_pending_closure(
    env: AuthenticatedEnv,
    filters: Annotated[FolioPendingSearch, Depends()],
    paging: Annotated[Paging, Depends(paging)],
    order: Annotated[str, Depends(folio_order)],
) -> PagedCollection[FolioSummary]:
    """Get folios pending closure: not fully invoiced OR not fully paid."""
    count, folios = (
        env["pms_api_folio.folio_router.helper"]
        .new()
        ._search(paging, filters, order=order)
    )
    return PagedCollection[FolioSummary](
        count=count,
        items=[FolioSummary.from_pms_folio(folio) for folio in folios],
    )


@pms_api_router.get(
    "/folios/pending-closure/count",
    response_model=FolioCountSummary,
    tags=["folio"],
)
async def count_folios_pending_closure(
    env: AuthenticatedEnv,
    filters: Annotated[FolioPendingSearch, Depends()],
) -> FolioCountSummary:
    """Get the counts of folios pending closure and their reservations."""
    folios_count, reservations_count = (
        env["pms_api_folio.folio_router.helper"].new().count(filters)
    )
    return FolioCountSummary(
        foliosCount=folios_count,
        reservationsCount=reservations_count,
    )
