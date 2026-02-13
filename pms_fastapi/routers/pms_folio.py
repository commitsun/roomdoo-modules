from typing import Annotated

from fastapi import Depends

from odoo import api, models
from odoo.osv import expression

from odoo.addons.extendable_fastapi.schemas import PagedCollection
from odoo.addons.fastapi.dependencies import (
    paging,
)
from odoo.addons.fastapi.schemas import Paging
from odoo.addons.pms.models.pms_folio import PmsFolio
from odoo.addons.pms_fastapi.dependencies import AuthenticatedEnv
from odoo.addons.pms_fastapi.models.fastapi_endpoint import pms_api_router
from odoo.addons.pms_fastapi.schemas.pms_folio import (
    FolioSearch,
    FolioSummary,
)
from odoo.addons.pms_fastapi.utils import FilteredModelAdapter


@pms_api_router.get(
    "/folios",
    response_model=PagedCollection[FolioSummary],
    tags=["folio"],
)
async def list_folios(
    env: AuthenticatedEnv,
    filters: Annotated[FolioSearch, Depends()],
    paging: Annotated[Paging, Depends(paging)],
) -> PagedCollection[FolioSummary]:
    """Get the list of the folios"""
    count, folios = (
        env["pms_api_folio.folio_router.helper"].new()._search(paging, filters)
    )

    return PagedCollection[FolioSummary](
        count=count,
        items=[FolioSummary.from_pms_folio(folio) for folio in folios],
    )


class PmsApiFolioRouterHelper(models.AbstractModel):
    _name = "pms_api_folio.folio_router.helper"
    _description = "Pms api folio Service Helper"

    def _get_domain_adapter(self):
        return [("reservation_type", "!=", "out"), ("state", "!=", "draft")]

    def _get_multicompany_rule(self):
        allowed_company_ids = self.env.user.company_ids.ids
        company_domain = expression.OR(
            [
                [("company_id", "=", False)],
                [("company_id", "in", allowed_company_ids)],
            ]
        )
        return company_domain

    @property
    def model_adapter(self) -> FilteredModelAdapter[PmsFolio]:
        base_domain = self._get_domain_adapter()
        multicompany_domain = self._get_multicompany_rule()
        model_domain = expression.AND([base_domain, multicompany_domain])
        return FilteredModelAdapter[PmsFolio](self.env, model_domain)

    def get(self, record_id) -> PmsFolio:
        return self.model_adapter.get(record_id)

    def _search(self, paging, params) -> tuple[int, PmsFolio]:
        return self.model_adapter.search_with_count(
            params.to_odoo_domain(self.env),
            limit=paging.limit,
            offset=paging.offset,
            context=params.to_odoo_context(self.env),
            order="create_date desc",
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
