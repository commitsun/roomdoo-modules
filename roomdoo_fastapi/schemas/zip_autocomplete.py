from odoo.addons.pms_fastapi.schemas.base import PmsBaseModel
from odoo.addons.pms_fastapi.schemas.country import CountryId
from odoo.addons.pms_fastapi.schemas.country_state import CountryStateId


class ZipSummary(PmsBaseModel):
    zip: str
    city: str | None = None
    state: CountryStateId | None = None
    country: CountryId | None = None

    @classmethod
    def from_res_city_zip(cls, res_city_zip_record):
        return ZipSummary(
            zip=res_city_zip_record.name,
            city=res_city_zip_record.city_id.name
            if res_city_zip_record.city_id
            else None,
            state=CountryStateId.from_res_country_state(res_city_zip_record.state_id)
            if res_city_zip_record.state_id
            else None,
            country=CountryId.from_res_country(res_city_zip_record.country_id)
            if res_city_zip_record.country_id
            else None,
        )
