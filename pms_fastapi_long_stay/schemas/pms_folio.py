from pydantic import Field

from odoo.addons.pms_fastapi.schemas import pms_folio


class FolioReservationLongStay(pms_folio.FolioReservation, extends=True):
    # See pms_reservation.py for the rationale on field naming
    # (auto-mapped snake_case vs. manually mapped Many2one).
    reservation_type: str | None = Field(None, alias="reservationType")
    is_long_stay_master: bool = Field(False, alias="isLongStayMaster")
    long_stay_group: int | None = Field(None, alias="longStayGroupId")

    @classmethod
    def from_pms_reservation(cls, reservation):
        res = super().from_pms_reservation(reservation)
        res.long_stay_group = reservation.long_stay_group_id.id or None
        return res
