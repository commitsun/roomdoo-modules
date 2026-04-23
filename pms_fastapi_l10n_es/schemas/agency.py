from odoo.addons.pms_fastapi.schemas import agency


class AgencyIdImageComercial(agency.AgencyIdImage, extends=True):
    @classmethod
    def parse_common_fields(cls, partner) -> dict:
        data = super().parse_common_fields(partner)
        data["name"] = partner.comercial or partner.name
        return data
