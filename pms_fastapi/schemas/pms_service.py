from .base import PmsBaseModel


class ServiceId(PmsBaseModel):
    id: int
    name: str

    @classmethod
    def from_pms_service(cls, service):
        filtered_data = cls._read_odoo_record(service)
        return cls(**filtered_data)
