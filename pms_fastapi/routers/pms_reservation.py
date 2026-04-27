from fastapi import Response
from fastapi.responses import JSONResponse

from odoo import fields, models
from odoo.exceptions import MissingError
from odoo.osv import expression

from odoo.addons.pms.models.pms_reservation import PmsReservation
from odoo.addons.pms_fastapi.dependencies import AuthenticatedEnv
from odoo.addons.pms_fastapi.models.fastapi_endpoint import pms_api_router
from odoo.addons.pms_fastapi.utils import FilteredModelAdapter

OFFBOARDING_INVALID_RESERVATION_STATES = ("draft", "cancel")


@pms_api_router.post(
    "/reservations/{reservation_id}/onboarding",
    status_code=204,
    tags=["reservation"],
)
async def onboarding(
    env: AuthenticatedEnv,
    reservation_id: int,
):
    """Confirm arrival for eligible guests of a reservation.

    Eligible guests are those with completed check-in data who have not yet
    arrived, on a reservation whose check-in date is today or earlier.
    Idempotent: returns 204 even if no guests are eligible.
    """
    return env["pms_api_reservation.router.helper"].new()._onboarding(reservation_id)


@pms_api_router.post(
    "/reservations/{reservation_id}/offboarding",
    status_code=204,
    tags=["reservation"],
)
async def offboarding(
    env: AuthenticatedEnv,
    reservation_id: int,
):
    """Confirm departure for eligible in-house guests of a reservation.

    Eligible guests are those currently in-house. Idempotent: returns 204
    even if no guests are eligible. Returns 409 if the reservation is in a
    state that does not allow confirming departures (e.g. draft, cancelled).
    """
    return env["pms_api_reservation.router.helper"].new()._offboarding(reservation_id)


class PmsApiReservationRouterHelper(models.AbstractModel):
    _name = "pms_api_reservation.router.helper"
    _description = "PMS API Reservation Router Helper"

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

    def get(self, record_id) -> PmsReservation:
        return self.model_adapter.get(record_id)

    def _reservation_not_found_response(self, reservation_id, action):
        return JSONResponse(
            status_code=404,
            content={
                "type": "/errors/reservation-not-found",
                "title": "Reservation not found",
                "status": 404,
                "detail": f"Reservation {reservation_id} does not exist.",
                "instance": f"/reservations/{reservation_id}/{action}",
            },
            media_type="application/problem+json",
        )

    def _onboarding(self, reservation_id):
        try:
            reservation = self.get(reservation_id)
        except MissingError:
            return self._reservation_not_found_response(reservation_id, "onboarding")

        today = fields.Date.today()
        eligible = reservation.checkin_partner_ids.filtered(
            lambda c: (
                c.state == "precheckin"
                and c.reservation_id.checkin <= today
                and c.reservation_id.checkout >= today
            )
        )
        if eligible:
            eligible.sudo().action_on_board()
        return Response(status_code=204)

    def _offboarding(self, reservation_id):
        try:
            reservation = self.get(reservation_id)
        except MissingError:
            return self._reservation_not_found_response(reservation_id, "offboarding")

        if reservation.state in OFFBOARDING_INVALID_RESERVATION_STATES:
            return JSONResponse(
                status_code=409,
                content={
                    "type": "/errors/reservation-state-invalid",
                    "title": (
                        "Reservation state does not allow " "confirming departures"
                    ),
                    "status": 409,
                    "detail": (
                        f"Reservation {reservation_id} is in "
                        f"'{reservation.state}' state and cannot be "
                        f"confirmed as departed."
                    ),
                    "instance": (f"/reservations/{reservation_id}/offboarding"),
                },
                media_type="application/problem+json",
            )

        eligible = reservation.checkin_partner_ids.filtered(
            lambda c: c.state == "onboard"
        )
        if eligible:
            eligible.sudo().action_done()
        return Response(status_code=204)
