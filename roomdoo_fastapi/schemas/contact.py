from pydantic import Field, computed_field

from odoo.addons.pms_fastapi.schemas import contact
from odoo.addons.pms_fastapi.schemas.country import CountryId
from odoo.addons.pms_fastapi.schemas.country_state import CountryStateId


class contactDetailResidenceAddress(contact.ContactDetail, extends=True):
    """Schema for contact detail with residence address."""

    residenceStreet: str = Field("", description="Residence street address")
    residenceStreet2: str = Field("", description="Residence street address line 2")
    residenceZip: str = Field("", description="Residence zip code")
    residenceCity: str = Field("", description="Residence city")
    residenceState: CountryStateId | None = Field(None, description="Residence state")
    residenceCountry: CountryId | None = Field(None, description="Residence country")

    @classmethod
    def from_res_partner(cls, partner):
        partner_record = super().from_res_partner(partner)
        residence_partner = partner.residence_partner_id
        partner_record.residenceStreet = residence_partner.street or ""
        partner_record.residenceStreet2 = residence_partner.street2 or ""
        partner_record.residenceZip = residence_partner.zip or ""
        partner_record.residenceCity = residence_partner.city or ""
        partner_record.residenceState = (
            CountryStateId.from_res_country_state(residence_partner.state_id)
            if residence_partner.state_id
            else None
        )
        partner_record.residenceCountry = (
            CountryId.from_res_country(residence_partner.country_id)
            if residence_partner.country_id
            else None
        )
        return partner_record


class ContactInsertResidenceAddress(contact.ContactInsert, extends=True):
    """Schema for inserting a contact with residence address."""

    residenceStreet: str = Field("", description="Residence street address")
    residenceStreet2: str = Field("", description="Residence street address line 2")
    residenceZip: str = Field("", description="Residence zip code")
    residenceCity: str = Field("", description="Residence city")
    residenceState: int | None = Field(None, description="Residence state")
    residenceCountry: int | None = Field(None, description="Residence country")

    @computed_field
    @property
    def is_same_address(self) -> bool:
        return not any(
            [
                self.residenceStreet != self.street,
                self.residenceStreet2 != self.street2,
                self.residenceZip != self.zip,
                self.residenceCity != self.city,
                self.residenceState != self.state_id,
                self.residenceCountry != self.country_id,
            ]
        )

    def to_res_partner(self, extra_exclude=None) -> dict:
        if not extra_exclude:
            extra_exclude = set()
        extra_exclude = extra_exclude.union(
            {
                "residenceStreet",
                "residenceStreet2",
                "residenceZip",
                "residenceCity",
                "residenceState",
                "residenceCountry",
                "is_same_address",
            }
        )
        return super().to_res_partner(extra_exclude=extra_exclude)
