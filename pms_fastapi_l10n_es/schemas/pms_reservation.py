from pydantic import Field

from odoo.addons.pms_fastapi.schemas import pms_folio


class reservationSummarySES(pms_folio.reservationSummary, extends=True):
    sesState: str = Field("")
    sesError: str = Field("")

    @classmethod
    def from_pms_reservation(cls, reservation):
        summary = super().from_pms_reservation(reservation)

        return summary
