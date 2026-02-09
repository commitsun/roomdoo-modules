from odoo.addons.pms_fastapi.schemas import pms_folio


class reservationSummarySES(pms_folio.reservationSummary, extends=True):
    sesState: str = None
    sesError: str = None

    @classmethod
    def from_pms_reservation(cls, reservation):
        summary = super().from_pms_reservation(reservation)

        return summary
