from pydantic import Field

from odoo.addons.pms_fastapi.schemas import pms_reservation


class ReservationSummaryLongStay(
    pms_reservation.reservationSummary, extends=True
):
    # ``reservation_type`` and ``is_long_stay_master`` are snake_case so they
    # are auto-mapped from the Odoo record by ``_read_odoo_record()``.
    # ``long_stay_group`` does NOT match the Odoo field name on purpose: the
    # Odoo field ``long_stay_group_id`` is a Many2one and ``.read()`` would
    # return ``[id, name]``, which would not validate as ``int``. Mapping it
    # manually keeps it out of the auto-read path.
    reservation_type: str | None = Field(None, alias="reservationType")
    is_long_stay_master: bool = Field(False, alias="isLongStayMaster")
    long_stay_group: int | None = Field(None, alias="longStayGroupId")

    @classmethod
    def from_pms_reservation(cls, reservation):
        res = super().from_pms_reservation(reservation)
        res.long_stay_group = reservation.long_stay_group_id.id or None
        return res
