from .base import PmsBaseModel


class ReservationId(PmsBaseModel):
    id: int
    name: str

    @classmethod
    def from_pms_reservation(cls, reservation):
        return ReservationId(id=reservation.id, name=reservation.name)
