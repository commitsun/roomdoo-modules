from odoo import models
from odoo.osv import expression

from odoo.addons.pms.models.pms_reservation import PmsReservation
from odoo.addons.pms_fastapi.dependencies import AuthenticatedEnv
from odoo.addons.pms_fastapi.models.fastapi_endpoint import pms_api_router
from odoo.addons.pms_fastapi.schemas.reservation_guest import ReservationGuest
from odoo.addons.pms_fastapi.utils import FilteredModelAdapter


@pms_api_router.get(
    "/reservations/{reservation_id}/guests",
    response_model=list[ReservationGuest],
    tags=["reservation"],
)
async def list_reservation_guests(
    env: AuthenticatedEnv,
    reservation_id: int,
) -> list[ReservationGuest]:
    """Get the list of guests associated with a reservation."""
    helper = env["pms_api_reservation_guest.router.helper"].new()
    checkin_partners = helper.get_reservation_guests(reservation_id)
    return [ReservationGuest.from_pms_checkin_partner(cp) for cp in checkin_partners]


class PmsApiReservationGuestRouterHelper(models.AbstractModel):
    _name = "pms_api_reservation_guest.router.helper"
    _description = "PMS API Reservation Guest Router Helper"

    def _get_domain_adapter(self):
        return []

    def _get_multicompany_rule(self):
        allowed_company_ids = self.env.user.company_ids.ids
        return expression.OR(
            [
                [("company_id", "=", False)],
                [("company_id", "in", allowed_company_ids)],
            ]
        )

    @property
    def model_adapter(self) -> FilteredModelAdapter[PmsReservation]:
        base_domain = self._get_domain_adapter()
        multicompany_domain = self._get_multicompany_rule()
        model_domain = expression.AND([base_domain, multicompany_domain])
        return FilteredModelAdapter[PmsReservation](self.env, model_domain)

    def get_reservation_guests(self, reservation_id):
        reservation = self.model_adapter.get(reservation_id)
        return reservation.checkin_partner_ids
